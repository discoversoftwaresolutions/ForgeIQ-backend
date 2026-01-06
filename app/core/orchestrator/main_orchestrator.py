# ==============================================
# 📁 core/orchestrator/main_orchestrator.py (V0.5 – Unified Control Plane)
# ==============================================
import os
import json
import asyncio
import logging
import uuid
import datetime
from typing import Dict, Any, Optional, List

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

from sdk.models import SDKMCPStrategyRequestContext, SDKMCPStrategyResponse
from interfaces.types.orchestration import OrchestrationFlowState
from interfaces.types.events import DagDefinition
from core.event_bus.redis_bus import EventBus, message_summary
from core.agent_registry import AgentRegistry
from core.shared_memory import SharedMemoryStore
from core.message_router import MessageRouter


# --- MCP Internal Components ---
from agents.MCP.strategy import MCPStrategyEngine
from agents.MCP.memory import get_mcp_memory
from agents.MCP.metrics import get_mcp_metrics
from agents.MCP.governance_bridge import send_proprietary_audit_event

# --- Observability ---
SERVICE_NAME_ORCHESTRATOR = "ForgeIQ.Orchestrator"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(SERVICE_NAME_ORCHESTRATOR)

_tracer = None
_trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing
    _tracer = setup_tracing(SERVICE_NAME_ORCHESTRATOR)
    _trace_api = otel_trace_api
except ImportError:
    logger.warning("Tracing unavailable")

# --- Retry Policy for MCP / SDK Calls ---
SDK_RETRY = retry(
    stop=stop_after_attempt(7),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=(
        retry_if_exception_type(httpx.RequestError) |
        retry_if_exception_type(httpx.HTTPStatusError)
    ),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True
)

# --- Exceptions ---
class OrchestrationError(Exception):
    def __init__(self, message: str, flow_id: Optional[str] = None, stage: Optional[str] = None):
        super().__init__(message)
        self.flow_id = flow_id
        self.stage = stage

    def __str__(self):
        return f"OrchestrationError(flow={self.flow_id}, stage={self.stage}): {self.args[0]}"

# ==============================================
# 🔁 ORCHESTRATOR (SINGLE SOURCE OF TRUTH)
# ==============================================
class Orchestrator:
    def __init__(
        self,
        agent_registry: AgentRegistry,
        event_bus: EventBus,
        shared_memory: SharedMemoryStore,
        message_router: MessageRouter,
        forgeiq_sdk_client: Optional[Any] = None,
    ):
        logger.info("Initializing ForgeIQ Unified Orchestrator (V0.5)")
        self.agent_registry = agent_registry
        self.event_bus = event_bus
        self.shared_memory = shared_memory
        self.message_router = message_router
        self.forgeiq_sdk_client = forgeiq_sdk_client

        if not forgeiq_sdk_client:
            logger.warning("ForgeIQ SDK client not provided — MCP optimization disabled")

    # ------------------------------
    # Tracing Helper
    # ------------------------------
    def _start_span(self, name: str, **attrs):
        if _tracer and _trace_api:
            span = _tracer.start_span(name)
            for k, v in attrs.items():
                span.set_attribute(k, v)
            return span
        class NoOpSpan:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def set_attribute(self, *a): pass
            def record_exception(self, *a): pass
            def set_status(self, *a): pass
        return NoOpSpan()

    # ------------------------------
    # Flow State Management
    # ------------------------------
    async def _update_flow_state(
        self,
        flow_id: str,
        updates: Dict[str, Any],
        initial_state_if_new: Optional[OrchestrationFlowState] = None,
    ):
        if not self.shared_memory.redis_client:
            logger.warning("Shared memory unavailable; cannot persist flow state")
            return

        key = f"orchestration_flow:{flow_id}"
        state = await self.shared_memory.get_value(key, expected_type=dict)

        if not state and initial_state_if_new:
            state = initial_state_if_new.copy()  # type: ignore
        elif state:
            state = OrchestrationFlowState(**state)  # type: ignore
        else:
            raise OrchestrationError("Flow state missing", flow_id)

        state.update(updates)  # type: ignore
        state["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"

        await self.shared_memory.set_value(key, state, expiry_seconds=86400)

    # ==========================================
    # 🧠 MCP STRATEGY OPTIMIZATION (Unified)
    # ==========================================
    async def request_and_apply_mcp_optimization(
        self,
        project_id: str,
        current_dag: DagDefinition,
        flow_id: str,
    ) -> Optional[DagDefinition]:

        if not self.forgeiq_sdk_client:
            raise OrchestrationError(
                "ForgeIQ SDK client unavailable",
                flow_id=flow_id,
                stage="MCP_INIT",
            )

        span = self._start_span(
            "mcp.optimize_dag",
            project_id=project_id,
            flow_id=flow_id,
        )

        await self._update_flow_state(
            flow_id,
            {"current_stage": f"REQUESTING_MCP_OPTIMIZATION_{current_dag['dag_id']}"},
        )

        try:
            with span:
                context = SDKMCPStrategyRequestContext(
                    project_id=project_id,
                    current_dag_snapshot=[dict(n) for n in current_dag.get("nodes", [])],
                    optimization_goal="general_build_efficiency",
                    additional_mcp_context={"flow_id": flow_id},
                )

                @SDK_RETRY
                async def _call():
                    return await self.forgeiq_sdk_client.request_mcp_build_strategy(
                        context=context
                    )

                response: SDKMCPStrategyResponse = await _call()

                if response and response.get("status") == "strategy_provided":
                    details = response.get("strategy_details")
                    raw_dag = details.get("new_dag_definition_raw") if details else None

                    if raw_dag:
                        optimized_dag = DagDefinition(**raw_dag)  # type: ignore
                        await self._update_flow_state(
                            flow_id,
                            {"current_stage": f"MCP_OPTIMIZATION_RECEIVED_{optimized_dag.get('dag_id')}"},
                        )

                        if _trace_api:
                            span.set_attribute("mcp.strategy_id", details.get("strategy_id"))
                            span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))

                        return optimized_dag

                await self._update_flow_state(
                    flow_id,
                    {"current_stage": "MCP_OPTIMIZATION_NO_DAG"},
                )
                return None

        except Exception as e:
            logger.error("MCP optimization failed", exc_info=True)
            await self._update_flow_state(
                flow_id,
                {"current_stage": "MCP_OPTIMIZATION_ERROR", "error_message": str(e)},
            )
            if _trace_api:
                span.record_exception(e)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR))
            raise OrchestrationError(str(e), flow_id=flow_id, stage="MCP_EXECUTION") from e
  # --- MCP Subsystems (Single Instances) ---
        self.mcp_strategy = MCPStrategyEngine()
        self.mcp_memory = get_mcp_memory()
        self.mcp_metrics = get_mcp_metrics()

        logger.info("MCP subsystems initialized (strategy, memory, metrics)")

        if not forgeiq_sdk_client:
            logger.warning("ForgeIQ SDK client not provided — MCP optimization via SDK disabled")
 
