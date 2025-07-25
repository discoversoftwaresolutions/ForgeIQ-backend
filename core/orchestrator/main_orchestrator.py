import uuid
import datetime
import logging
from typing import Optional, Dict, Any, List
# Assuming forgeiq_sdk.models is available and contains DagDefinition, SDKMCPStrategyRequestContext, SDKMCPStrategyResponse
from forgeiq_sdk.models import DagDefinition, SDKMCPStrategyRequestContext, SDKMCPStrategyResponse
from .exceptions import OrchestrationError # Assuming this custom exception exists
from .trace_utils import _tracer, _trace_api, start_trace_span_if_available # Assuming OpenTelemetry setup
from .utils import message_summary
from .state import OrchestrationFlowState # Assuming this handles internal state updates

# --- NEW IMPORTS FOR RETRIES AND ASYNC HTTP ---
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

logger = logging.getLogger(__name__) # Use specific logger for this module

# --- Define common retry strategy for SDK Client calls ---
# This applies to calls made by self.forgeiq_sdk_client which internally uses httpx.
# Retries on network errors, server-side HTTP errors (5xx), and rate limits (429).
SDK_CLIENT_RETRY_STRATEGY = retry(
    stop=stop_after_attempt(7), # More attempts for critical internal SDK calls
    wait=wait_exponential(multiplier=1, min=2, max=60), # Exponential backoff up to 60s
    retry=(
        retry_if_exception_type(httpx.HTTPStatusError) |
        retry_if_exception_type(httpx.RequestError)
    ),
    retry_error_codes={429, 500, 502, 503, 504}, # Specific transient HTTP errors
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True # Re-raise the exception after exhausting retries
)

class Orchestrator:
    def __init__(self, forgeiq_sdk_client, message_router):
        self.forgeiq_sdk_client = forgeiq_sdk_client # This client is assumed to be async (using httpx)
        self.message_router = message_router
        # Assume _update_flow_state is a private method or part of self.state_manager
        # For this example, let's assume it's part of self and needs to be defined
        # or replaced by proper state management.
        self._update_flow_state = self._get_flow_state_updater()


    # Placeholder/conceptual method for _update_flow_state.
    # In a real app, this would interact with ForgeIQ's database (via SQLAlchemy)
    # and its Redis Pub/Sub (via autosoft_utils for ForgeIQ)
    def _get_flow_state_updater(self):
        async def update_stub(flow_id: str, updates: Dict[str, Any]):
            logger.info(f"ForgeIQ Internal Flow State Update for {flow_id}: {updates}")
            # TODO: Implement actual DB update for ForgeIQ's internal flow state
            # TODO: Implement Redis Pub/Sub for ForgeIQ's internal flow status updates
            pass # Replace with actual DB/Redis logic
        return update_stub

    async def request_mcp_strategy_optimization(
        self, project_id: str, current_dag: Optional[DagDefinition] = None
    ) -> Optional[Dict[str, Any]]: # Changed return type to Optional[Dict]
        if not self.forgeiq_sdk_client:
            logger.error(f"SDK client not available to request MCP strategy for '{project_id}'.")
            raise OrchestrationError(f"ForgeIQ SDK client unavailable for project '{project_id}'.") # Raise specific error

        span = start_trace_span_if_available("request_mcp_strategy", project_id=project_id)
        logger.info(f"Requesting MCP build strategy optimization for project '{project_id}'.")

        try:
            with span:  # type: ignore
                # Apply retry strategy to the SDK client call
                @SDK_CLIENT_RETRY_STRATEGY
                async def _call_mcp_strategy():
                    # Assuming forgeiq_sdk_client.request_mcp_build_strategy internally uses httpx
                    mcp_response = await self.forgeiq_sdk_client.request_mcp_build_strategy(
                        project_id=project_id,
                        current_dag_info=current_dag if current_dag else None
                    )
                    # The SDK client's method should raise for status, if not, do it here
                    # mcp_response.raise_for_status() # If SDK returns httpx.Response directly
                    return mcp_response # Assume SDK returns parsed JSON/Dict

                mcp_response_data = await _call_mcp_strategy()

                if mcp_response_data:
                    logger.info(f"Received strategy from MCP: {message_summary(mcp_response_data)}")
                    if _trace_api and span:
                        span.set_attribute("mcp.response_status", mcp_response_data.get("status"))
                        span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                    return mcp_response_data
                else:
                    logger.warning(f"No valid MCP response received for '{project_id}'.")
                    if _trace_api and span:
                        span.set_attribute("mcp.response_received", False)
                        span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "No valid MCP response"))
                    raise OrchestrationError(f"No valid MCP response for project '{project_id}'.") # Raise specific error
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
        self, project_id: str, current_dag: DagDefinition, flow_id: str
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

                # Apply retry strategy to the SDK client call
                @SDK_CLIENT_RETRY_STRATEGY
                async def _call_mcp_optimization():
                    response: SDKMCPStrategyResponse = await self.forgeiq_sdk_client.request_mcp_build_strategy(
                        context=request_context # type: ignore
                    )
                    # The SDK client's method should raise for status, if not, do it here
                    # response.raise_for_status() # If SDK returns httpx.Response directly
                    return response # Assume SDK returns parsed Pydantic model

                response_data: SDKMCPStrategyResponse = await _call_mcp_optimization()

                if (
                    response_data and response_data.status == "strategy_provided"
                    and response_data.strategy_details
                ):
                    strategy = response_data.strategy_details
                    new_raw_dag = strategy.new_dag_definition_raw

                    if new_raw_dag and isinstance(new_raw_dag, dict):
                        new_dag = DagDefinition(**new_raw_dag)
                        logger.info(f"Flow {flow_id}: Optimized DAG '{new_dag.dag_id}' received.")
                        if _trace_api and span:
                            span.set_attribute("mcp.strategy_id", strategy.strategy_id)
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
                        return None # No new DAG definition, but optimization might provide directives
                else:
                    msg = f"MCP strategy unavailable or invalid for '{project_id}': {response_data.message if response_data else 'No response'}"
                    logger.warning(f"Flow {flow_id}: {msg}")
                    if _trace_api and span:
                        span.set_attribute("mcp.error", msg)
                    await self._update_flow_state(flow_id, {"current_stage": "MCP_OPTIMIZATION_FAILED_OR_NO_STRATEGY"})
                    raise OrchestrationError(f"MCP strategy unavailable or invalid: {msg}") # Raise specific error

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
