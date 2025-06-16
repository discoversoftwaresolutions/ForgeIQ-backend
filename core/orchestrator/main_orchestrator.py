import uuid
import datetime
import logging
from typing import Optional, Dict, Any, List
from forgeiq_sdk.models import DagDefinition, SDKMCPStrategyRequestContext, SDKMCPStrategyResponse
from .exceptions import OrchestrationError
from .trace_utils import _tracer, _trace_api, start_trace_span_if_available
from .utils import message_summary
from .state import OrchestrationFlowState

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, forgeiq_sdk_client, message_router):
        self.forgeiq_sdk_client = forgeiq_sdk_client
        self.message_router = message_router

    async def request_mcp_strategy_optimization(
        self, project_id: str, current_dag: Optional[DagDefinition] = None
    ) -> Optional[Dict[str, Any]]:
        if not self.forgeiq_sdk_client:
            logger.error(f"SDK client not available to request MCP strategy for '{project_id}'.")
            return None

        span = _start_trace_span_if_available("request_mcp_strategy", project_id=project_id)
        logger.info(f"Requesting MCP build strategy optimization for project '{project_id}'.")

        try:
            with span:  # type: ignore
                dag_nodes = current_dag.nodes if current_dag else None
                mcp_response = await self.forgeiq_sdk_client.request_mcp_build_strategy(
                    project_id=project_id,
                    current_dag_info=current_dag if current_dag else None
                )

                if mcp_response:
                    logger.info(f"Received strategy from MCP: {message_summary(mcp_response)}")
                    if _trace_api and span:
                        span.set_attribute("mcp.response_status", mcp_response.get("status"))
                        span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                    return mcp_response
                else:
                    logger.warning(f"No MCP response for '{project_id}'.")
                    if _trace_api and span:
                        span.set_attribute("mcp.response_received", False)
                    return None
        except Exception as e:
            logger.error(f"Error requesting MCP strategy for '{project_id}': {e}", exc_info=True)
            if _trace_api and span:
                span.record_exception(e)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR))
            return None

    async def request_and_apply_mcp_optimization(
        self, project_id: str, current_dag: DagDefinition, flow_id: str
    ) -> Optional[DagDefinition]:
        if not self.forgeiq_sdk_client:
            logger.error(f"Flow {flow_id}: SDK client not available.")
            return None

        span = _start_trace_span_if_available(
            "request_mcp_optimization",
            project_id=project_id,
            flow_id=flow_id,
            mcp_task="optimize_dag"
        )

        await self._update_flow_state(
            flow_id,
            {"current_stage": f"REQUESTING_MCP_OPTIMIZATION_FOR_DAG_{current_dag.dag_id}"}
        )

        try:
            with span:  # type: ignore
                request_context = SDKMCPStrategyRequestContext(
                    project_id=project_id,
                    current_dag_snapshot=[dict(node) for node in current_dag.nodes or []],
                    optimization_goal="general_build_efficiency",
                    additional_mcp_context={"triggering_flow_id": flow_id}
                )

                response: SDKMCPStrategyResponse = await self.forgeiq_sdk_client.request_mcp_build_strategy(
                    context=request_context  # type: ignore
                )

                if (
                    response and response.get("status") == "strategy_provided"
                    and response.get("strategy_details")
                ):
                    strategy = response["strategy_details"]
                    new_raw_dag = strategy.get("new_dag_definition_raw")

                    if new_raw_dag and isinstance(new_raw_dag, dict):
                        new_dag = DagDefinition(**new_raw_dag)
                        logger.info(f"Flow {flow_id}: Optimized DAG '{new_dag.dag_id}' received.")
                        if _trace_api and span:
                            span.set_attribute("mcp.strategy_id", strategy.get("strategy_id"))
                            span.set_attribute("mcp.new_dag_id", new_dag.dag_id)
                            span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                        await self._update_flow_state(
                            flow_id,
                            {"current_stage": f"MCP_OPTIMIZATION_RECEIVED_DAG_{new_dag.dag_id}"}
                        )
                        return new_dag
                    else:
                        logger.info(f"Flow {flow_id}: Strategy returned without new DAG.")
                        if _trace_api and span:
                            span.set_attribute("mcp.directives_only", True)
                else:
                    msg = f"MCP strategy unavailable or invalid for '{project_id}'."
                    logger.warning(f"Flow {flow_id}: {msg}")
                    if _trace_api and span:
                        span.set_attribute("mcp.error", msg)

                await self._update_flow_state(flow_id, {"current_stage": "MCP_OPTIMIZATION_NO_NEW_DAG"})
        except Exception as e:
            logger.error(f"Flow {flow_id}: MCP optimization error: {e}", exc_info=True)
            await self._update_flow_state(
                flow_id,
                {"current_stage": "MCP_OPTIMIZATION_ERROR", "error_message": str(e)}
            )
            if _trace_api and span:
                span.record_exception(e)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR))

        return None
