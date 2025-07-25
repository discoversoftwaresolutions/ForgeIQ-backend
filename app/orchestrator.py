# ================================================
# 📁 forgeiq-backend/app/orchestrator.py (V0.3)
# (This file was previously labeled agents/MCP/controller.py during discussion,
# but its final assumed location and role in ForgeIQ Backend is here.)
# ================================================
import os
import logging
import asyncio
import json
import datetime # ADDED: Used for datetime.datetime.utcnow()
import uuid     # ADDED: Used for uuid.uuid4()
from typing import Dict, Any, Optional, List, cast

# --- NEW IMPORTS FOR RETRIES AND ASYNC HTTP ---
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

# === ASSUMED FORGEIQ SDK MODELS ===
# These should be available from your `forgeiq_sdk` package or similar.
# Ensure forgeiq_sdk is installed/available in the environment.
try:
    from forgeiq_sdk.models import DagDefinition, SDKMCPStrategyRequestContext, SDKMCPStrategyResponse
except ImportError:
    # Fallback for conceptual testing if SDK is not installed.
    logging.getLogger(__name__).warning("forgeiq_sdk.models not found. Using dummy classes.")
    class DagDefinition:
        def __init__(self, nodes: List[Dict]): self.nodes = nodes; self.dag_id = "mock_dag"
        def dict(self): return {"nodes": self.nodes, "dag_id": self.dag_id}
        def get(self, key, default=None): return getattr(self, key, default) # Added for consistency
    class SDKMCPStrategyRequestContext(BaseModel): # Using BaseModel from pydantic for type consistency
        project_id: str
        current_dag_snapshot: Optional[List[Dict[str, Any]]] = None
        optimization_goal: Optional[str] = None
        additional_mcp_context: Optional[Dict[str, Any]] = None
    class SDKMCPStrategyResponse(BaseModel): # Using BaseModel for type consistency
        status: str
        message: Optional[str] = None
        strategy_details: Optional[Dict[str, Any]] = None
        def get(self, key, default=None): return getattr(self, key, default)


# --- Internal MCP Components (relative imports, assuming structure) ---
# Ensure these files exist in agents/MCP/ and have their __init__.py files.
from agents.MCP.strategy import MCPStrategyEngine
from agents.MCP.memory import get_mcp_memory
from agents.MCP.metrics import get_mcp_metrics
from agents.MCP.governance_bridge import send_proprietary_audit_event


# --- Observability & Initialization (OpenTelemetry) ---
SERVICE_NAME_MCP_ORCHESTRATOR = "ForgeIQ.MCPOrchestrator" # More specific name
logger = logging.getLogger(__name__)

_tracer = None
_trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    _tracer = otel_trace_api.get_tracer(SERVICE_NAME_MCP_ORCHESTRATOR, "0.2.0")
    _trace_api = otel_trace_api
except ImportError:
    logger.info(f"{SERVICE_NAME_MCP_ORCHESTRATOR}: OpenTelemetry not available.")
    # Fallback for _trace_api if OpenTelemetry is not installed
    # This ensures code paths that use _trace_api don't error out
    class _NoOpTraceAPI:
        class Status:
            def __init__(self, code, description=None): pass
        class StatusCode:
            OK = "OK"
            ERROR = "ERROR"
        def get_current_span_context(self): return None
        # Corrected NoOpContextManager and Status class from previous main.py fix
        def Status(self, code, description=None):
            class NoOpStatus: pass
            return NoOpStatus()
        def NoOpContextManager(self):
            class NoOpCM:
                def __enter__(self): return None
                def __exit__(self, exc_type, exc_val, exc_tb): pass
            return NoOpCM()
    _trace_api = _NoOpTraceAPI() # Assign the NoOpTraceAPI instance


# --- Global MCP Strategy & Memory Instances ---
mcp_strategy = MCPStrategyEngine()
mcp_memory = get_mcp_memory() # Assuming this function returns a singleton/instance
mcp_metrics = get_mcp_metrics() # Assuming this function returns a singleton/instance

# --- Custom Exception (as discussed) ---
class OrchestrationError(Exception):
    """Custom exception for errors during MCP orchestration."""
    pass

# --- Internal Utility (conceptual) ---
def message_summary(response: Dict[str, Any]) -> str:
    """Provides a brief summary of a response for logging."""
    return f"Status: {response.get('status', 'N/A')}, Message: {response.get('message', 'N/A')[:50]}..."

def start_trace_span_if_available(operation_name: str, parent_span_context: Optional[Any] = None, **attrs):
    if _tracer and _trace_api:
        span = _tracer.start_span(operation_name, context=parent_span_context)
        for k,v_attr in attrs.items(): span.set_attribute(k, v_attr)
        return span
    # Corrected NoOpSpan class syntax (each def on new line, consistent indentation)
    class NoOpSpan:
        def __enter__(self):
            return self
        def __exit__(self, et, ev, tb):
            pass
        def set_attribute(self, k, v):
            pass
        def record_exception(self, exception, attributes=None):
            pass
        def set_status(self, status):
            pass
        def end(self):
            pass
    return NoOpSpan()


# --- Define common retry strategy for SDK Client calls ---
SDK_CLIENT_RETRY_STRATEGY = retry(
    stop=stop_after_attempt(7),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=(
        retry_if_exception_type(httpx.HTTPStatusError) |
        retry_if_exception_type(httpx.RequestError)
    ),
    retry_error_codes={429, 500, 502, 503, 504},
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True
)

class Orchestrator:
    """Orchestrates strategic optimization and governance-driven task execution within MCP."""

    def __init__(self, forgeiq_sdk_client: Any, message_router: Any = None):
        logger.info("🚀 MCPController (Orchestrator) Initializing...")
        self.forgeiq_sdk_client = forgeiq_sdk_client
        self.message_router = message_router
        self.retention_limit = 100
        self._update_flow_state = self._get_flow_state_updater()
        logger.info("✅ MCPController (Orchestrator) Initialized.")

    # Placeholder/conceptual method for _update_flow_state.
    def _get_flow_state_updater(self):
        async def update_stub(flow_id: str, updates: Dict[str, Any]):
            logger.info(f"ForgeIQ Internal Flow State Update for {flow_id}: {updates}")
            # TODO: Implement actual DB update for ForgeIQ's internal flow state
            #       This would use forgeiq_utils.update_forgeiq_task_state_and_notify
            #       Or pass this specific updater from a higher level context
            pass
        return update_stub

    async def request_mcp_strategy_optimization(
        self,
        project_id: str,
        current_dag: Optional[DagDefinition] = None # DagDefinition now imported, assume Pydantic
    ) -> Dict[str, Any]: # Using Dict[str, Any] as return type for consistency with main.py
        if not self.forgeiq_sdk_client:
            logger.error(f"SDK client not available to request MCP strategy for '{project_id}'.")
            raise OrchestrationError(f"ForgeIQ SDK client unavailable for project '{project_id}'.")

        span = start_trace_span_if_available("request_mcp_strategy", project_id=project_id)
        logger.info(f"Requesting MCP build strategy optimization for project '{project_id}'.")

        try:
            with span:
                @SDK_CLIENT_RETRY_STRATEGY
                async def _call_mcp_strategy():
                    mcp_response = await self.forgeiq_sdk_client.request_mcp_build_strategy(
                        project_id=project_id,
                        # Pass DagDefinition object if current_dag is instance, otherwise pass as is
                        current_dag_info=current_dag.dict() if isinstance(current_dag, DagDefinition) else current_dag
                    )
                    return mcp_response

                mcp_response_data = await _call_mcp_strategy()

                if mcp_response_data:
                    # Accessing Pydantic model properties directly if SDKMCPStrategyResponse is Pydantic
                    response_status = getattr(mcp_response_data, "status", mcp_response_data.get("status"))
                    logger.info(f"Received strategy from MCP: {message_summary(mcp_response_data)}")
                    if _trace_api and span:
                        span.set_attribute("mcp.response_status", response_status)
                        span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                    # Return as dict if main.py expects dict
                    return mcp_response_data.dict() if isinstance(mcp_response_data, BaseModel) else mcp_response_data
                else:
                    logger.warning(f"No valid MCP response received for '{project_id}'.")
                    if _trace_api and span:
                        span.set_attribute("mcp.response_received", False)
                        span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "No valid MCP response"))
                    raise OrchestrationError(f"No valid MCP response for project '{project_id}'.")
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error requesting MCP strategy for '{project_id}': {e.response.status_code} - {e.response.text[:200]}", exc_info=True)
            if _trace_api and span:
                span.record_exception(e)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"HTTP Error {e.response.status_code}"))
            raise OrchestrationError(f"MCP service HTTP error: {e.response.status_code} - {e.response.text[:200]}") from e
        except httpx.RequestError as e:
            logger.error(f"Network error requesting MCP strategy for '{project_id}': {e}", exc_info=True)
            if _trace_api and span:
                span.record_exception(e)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Network Error"))
            raise OrchestrationError(f"MCP service network error: {str(e)}") from e
        except Exception as e:
            logger.error(f"Unhandled error requesting MCP strategy for '{project_id}': {e}", exc_info=True)
            if _trace_api and span:
                span.record_exception(e)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Unhandled Exception"))
            raise OrchestrationError(f"Unhandled error during MCP strategy request: {str(e)}") from e


    async def request_and_apply_mcp_optimization(
        self,
        project_id: str,
        current_dag: DagDefinition, # Assume DagDefinition is a Pydantic model
        flow_id: str
    ) -> Optional[DagDefinition]:
        if not self.forgeiq_sdk_client:
            logger.error(f"Flow {flow_id}: SDK client not available for MCP optimization.")
            raise OrchestrationError(f"ForgeIQ SDK client unavailable for flow '{flow_id}'.")

        span = start_trace_span_if_available(
            "request_mcp_optimization",
            project_id=project_id,
            flow_id=flow_id,
            mcp_task="optimize_dag"
        )

        # Access DagDefinition properties directly, assuming it's a Pydantic model
        await self._update_flow_state(
            flow_id,
            {"current_stage": f"REQUESTING_MCP_OPTIMIZATION_FOR_DAG_{current_dag.dag_id}"}
        )

        try:
            with span:
                # current_dag.nodes should be iterable, and its items dict-convertible
                current_dag_snapshot_data = [node.dict() if isinstance(node, BaseModel) else dict(node) for node in current_dag.nodes or []]

                request_context = SDKMCPStrategyRequestContext(
                    project_id=project_id,
                    current_dag_snapshot=current_dag_snapshot_data,
                    optimization_goal="general_build_efficiency",
                    additional_mcp_context={"triggering_flow_id": flow_id}
                )

                @SDK_CLIENT_RETRY_STRATEGY
                async def _call_mcp_optimization():
                    response: SDKMCPStrategyResponse = await self.forgeiq_sdk_client.request_mcp_build_strategy(
                        context=request_context
                    )
                    return response

                response_data: SDKMCPStrategyResponse = await _call_mcp_optimization()

                # Access Pydantic model properties directly
                response_status = response_data.status
                strategy_details = response_data.strategy_details

                if (
                    response_data and response_status == "strategy_provided"
                    and strategy_details
                ):
                    new_raw_dag = strategy_details.get("new_dag_definition_raw")

                    if new_raw_dag and isinstance(new_raw_dag, dict):
                        # Validate and create new DagDefinition from raw dict
                        new_dag = DagDefinition(**new_raw_dag)
                        logger.info(f"Flow {flow_id}: Optimized DAG '{new_dag.dag_id}' received.")
                        if _trace_api and span:
                            span.set_attribute("mcp.strategy_id", strategy_details.get("strategy_id"))
                            span.set_attribute("mcp.new_dag_id", new_dag.dag_id)
                            span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                        await self._update_flow_state(
                            flow_id,
                            {"current_stage": f"MCP_OPTIMIZATION_RECEIVED_DAG_{new_dag.dag_id}"}
                        )
                        return new_dag
                    else:
                        logger.info(f"Flow {flow_id}: Strategy returned without new DAG definition.")
                        if _trace_api and span:
                            span.set_attribute("mcp.directives_only", True)
                        await self._update_flow_state(flow_id, {"current_stage": "MCP_OPTIMIZATION_NO_NEW_DAG"})
                        return None
                else:
                    msg = f"MCP strategy unavailable or invalid for '{project_id}': {response_data.message if response_data else 'No response'}"
                    logger.warning(f"Flow {flow_id}: {msg}")
                    if _trace_api and span:
                        span.set_attribute("mcp.error", msg)
                    await self._update_flow_state(flow_id, {"current_stage": "MCP_OPTIMIZATION_FAILED_OR_NO_STRATEGY"})
                    raise OrchestrationError(f"MCP strategy unavailable or invalid: {msg}")

        except httpx.HTTPStatusError as e:
            logger.error(f"Flow {flow_id}: HTTP error during MCP optimization: {e.response.status_code} - {e.response.text[:200]}", exc_info=True)
            if _trace_api and span:
                span.record_exception(e)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"HTTP Error {e.response.status_code}"))
            raise OrchestrationError(f"MCP optimization HTTP error: {e.response.status_code} - {e.response.text[:200]}") from e
        except httpx.RequestError as e:
            logger.error(f"Flow {flow_id}: Network error during MCP optimization: {e}", exc_info=True)
            if _trace_api and span:
                span.record_exception(e)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Network Error"))
            raise OrchestrationError(f"MCP optimization network error: {str(e)}") from e
        except OrchestrationError: # Re-raise if our own OrchestrationError was raised
            raise
        except Exception as e:
            logger.error(f"Flow {flow_id}: Unhandled error during MCP optimization: {e}", exc_info=True)
            if _trace_api and span:
                span.record_exception(e)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Unhandled Exception"))
            raise OrchestrationError(f"Unhandled error during MCP optimization: {str(e)}") from e
