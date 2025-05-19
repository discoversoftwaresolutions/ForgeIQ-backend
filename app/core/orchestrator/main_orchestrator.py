# ==============================================
# 📁 core/orchestrator/main_orchestrator.py (V0.3)
# ==============================================
import os
import json
import asyncio
import logging
import uuid
import datetime
import time  # For _wait_for_event
from typing import Dict, Any, Optional, List
from sdk.models import SDKMCPStrategyRequestContext, SDKMCPStrategyResponse

# --- Observability Setup ---
SERVICE_NAME_ORCHESTRATOR = "Orchestrator"
LOG_LEVEL_ORCHESTRATOR = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL_ORCHESTRATOR,
    format=f'%(asctime)s - {SERVICE_NAME_ORCHESTRATOR} - %(name)s - %(levelname)s - %(message)s'
)

_tracer = None
_trace_api = None

try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing
    _tracer = setup_tracing(SERVICE_NAME_ORCHESTRATOR)
    _trace_api = otel_trace_api
except ImportError as e:
    logging.warning(f"Orchestrator: Tracing setup failed. Error: {e}", exc_info=True)

logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from core.agent_registry import AgentRegistry
from core.shared_memory import SharedMemoryStore
from core.message_router import MessageRouter, MessageRouteNotFoundError, InvalidMessagePayloadError

from interfaces.types.events import (
    FileChange, NewCommitEvent,  # For run_full_build_flow
    PipelineGenerationRequestEvent, PipelineGenerationUserPrompt,  # For BuildSurf via MessageRouter
    DagDefinition, DagDefinitionCreatedEvent, DagExecutionStatusEvent,  # For PlanAgent interaction
    DeploymentRequestEvent, DeploymentStatusEvent,  # For CI_CD_Agent via MessageRouter
    AffectedTasksIdentifiedEvent, SecurityScanResultEvent
)
from interfaces.types.orchestration import OrchestrationFlowState

# Import SDK client and relevant request/response models for code generation
try:
    from sdk.client import ForgeIQClient
    from apps.forgeiq_backend.app.api_models import (
        CodeGenerationPrompt, CodeGenerationResponse, GeneratedCodeOutput
    )
except ImportError as e_sdk:
    logger.error(f"Orchestrator: Failed to import ForgeIQ SDK or its models: {e_sdk}. Code generation flow will be impacted.", exc_info=True)
    ForgeIQClient = None  # type: ignore
    CodeGenerationPrompt = None  # type: ignore
    CodeGenerationResponse = None  # type: ignore

# --- Configuration Defaults ---
DEFAULT_EVENT_TIMEOUT = int(os.getenv("ORCHESTRATOR_EVENT_TIMEOUT_SECONDS", 300))  # 5 minutes
FLOW_STATE_EXPIRY_SECONDS = int(os.getenv("ORCHESTRATOR_FLOW_STATE_EXPIRY_SECONDS", 24 * 60 * 60))  # 1 day

# --- Custom Exception ---
class OrchestrationError(Exception):
    """Custom exception for orchestration failures."""

    def __init__(self, message: str, flow_id: Optional[str] = None, stage: Optional[str] = None):
        super().__init__(message)
        self.flow_id = flow_id
        self.stage = stage
        self.message = message

    def __str__(self):
        return f"OrchestrationError (Flow: {self.flow_id}, Stage: {self.stage}): {self.message}"

# --- Orchestrator Class ---
class Orchestrator:
    def __init__(self, 
                 agent_registry: AgentRegistry, 
                 event_bus: EventBus, 
                 shared_memory: SharedMemoryStore,
                 message_router: MessageRouter,
                 forgeiq_sdk_client: Optional[ForgeIQClient] = None):
        """Initializes the Orchestrator."""
        logger.info("Initializing Orchestrator (V0.3 with Code Generation Flow)...")
        
        self.agent_registry = agent_registry
        self.event_bus = event_bus
        self.shared_memory = shared_memory
        self.message_router = message_router
        self.forgeiq_sdk_client = forgeiq_sdk_client

        # Validate core dependencies
        failed_services = [svc for svc in ["event_bus", "shared_memory", "message_router", "agent_registry"] if not getattr(self, svc)]
        if failed_services:
            logger.error(f"Orchestrator critical: Missing core services -> {failed_services}")

        # Validate SDK client initialization
        if not self.forgeiq_sdk_client and ForgeIQClient is not None:
            logger.warning("Orchestrator: ForgeIQ SDK client not provided during initialization. Code generation flow will fail if attempted.")
        elif ForgeIQClient is None:
            logger.error("Orchestrator: ForgeIQClient class could not be imported. Code generation flow is unavailable.")

        logger.info("Orchestrator V0.3 Initialized.")

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        """Helper method for tracing integration."""
        if _tracer and _trace_api:
            with _tracer.start_as_current_span(operation_name) as span:
                for key, value in attrs.items():
                    span.set_attribute(key, value)
                return span
        return None
        if _tracer and _trace_api:
            span = _tracer.start_span(f"orchestrator.{operation_name}", context=parent_context)
            for k,v_attr in attrs.items(): span.set_attribute(k, v_attr) # Use different var name
            return span
        class NoOpSpan: # Ensure this no-op class is defined to prevent errors if tracer is None
            def __enter__(self): return self
            def __exit__(self, exc_type, exc_val, exc_tb): pass
            def set_attribute(self, key, value): pass
            def record_exception(self, exception, attributes=None): pass
            def set_status(self, status): pass
            def end(self): pass
        return NoOpSpan()


    async def _update_flow_state(self, flow_id: str, updates: Dict[str, Any],
                                 initial_state_if_new: Optional[OrchestrationFlowState] = None):
        if not self.shared_memory.redis_client:
            logger.warning(f"Flow {flow_id}: Cannot update state, SharedMemoryStore not connected.")
            return

        state_key = f"orchestration_flow:{flow_id}"
        # Use a lock for read-modify-write safety on the flow state
        # This is a conceptual lock; Redis transactions or Lua scripts are better for true atomicity.
        # For V0.1, simple get then set.

        current_state_dict = await self.shared_memory.get_value(state_key, expected_type=dict)

        if not current_state_dict and initial_state_if_new:
            current_state = initial_state_if_new.copy() # type: ignore # Start with initial state
        elif current_state_dict:
            # Ensure all keys from OrchestrationFlowState are present if possible, or handle partials
            current_state = OrchestrationFlowState(**current_state_dict) # type: ignore
        else: # No current state and no initial state provided for update
            logger.error(f"Flow {flow_id}: Cannot update state, no existing state found and no initial state provided.")
            return

        current_state.update(updates) # type: ignore 
        current_state["updated_at"] = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"

        await self.shared_memory.set_value(state_key, current_state, expiry_seconds=FLOW_STATE_EXPIRY_SECONDS)
        logger.debug(f"Flow {flow_id}: State updated - Stage: {current_state.get('current_stage')}, Status: {current_state.get('status')}")


    async def _wait_for_event(self, # ... (This method remains the same as in response #77) ...
                              expected_event_type: str, correlation_id: str, correlation_id_field_in_event: str,
                              event_channel_pattern: str, timeout_seconds: int = DEFAULT_EVENT_TIMEOUT
                             ) -> Optional[Dict[str, Any]]:
        # ... (implementation from response #77)
        if not self.event_bus.redis_client: logger.error(f"Cannot wait for event '{expected_event_type}': EventBus not connected."); return None
        pubsub = None; span_wait = self._start_trace_span_if_available("wait_for_event", event_type=expected_event_type, correlation_id=correlation_id)
        try:
            with span_wait: #type: ignore
                pubsub = self.event_bus.subscribe_to_channel(event_channel_pattern) #type: ignore
                if not pubsub: logger.error(f"Failed to subscribe to '{event_channel_pattern}'"); return None
                logger.info(f"Orch waiting for '{expected_event_type}' with {correlation_id_field_in_event}='{correlation_id}' on '{event_channel_pattern}'")
                start_wait_time = time.monotonic()
                while True:
                    if time.monotonic() - start_wait_time > timeout_seconds: logger.warning(f"Timeout for '{expected_event_type}'."); return None
                    message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0) #type: ignore
                    if message and message["type"] == "pmessage":
                        try:
                            event_data = json.loads(message["data"].decode('utf-8')) #type: ignore
                            if event_data.get("event_type") == expected_event_type and event_data.get(correlation_id_field_in_event) == correlation_id:
                                logger.info(f"Orch received '{expected_event_type}': {message_summary(event_data)}")
                                return event_data
                        except Exception as e: logger.error(f"Orch error processing message: {e}")
                    await asyncio.sleep(0.2)
        finally:
            if pubsub:
                try:
                    if hasattr(pubsub, 'punsubscribe'): await asyncio.to_thread(pubsub.punsubscribe, event_channel_pattern) #type: ignore
                    if hasattr(pubsub, 'close'): await asyncio.to_thread(pubsub.close) #type: ignore
                except Exception as e_close: logger.error(f"Error closing pubsub: {e_close}")
        return None

    async def run_full_build_flow(self, project_id: str, commit_sha: str, 
                                  changed_files_list: List[str], # Simple list of paths
                                  user_prompt_for_pipeline: Optional[str] = None,
                                  flow_id_override: Optional[str] = None):
        # ... (This extensive method from V0.2 - response #77 - remains largely the same, 
        #      using self.message_router for dispatches and self._update_flow_state) ...
        # For brevity, not re-pasting; key is it uses MessageRouter and _update_flow_state.
        flow_id = flow_id_override or f"build_flow_{project_id}_{commit_sha[:7]}_{str(uuid.uuid4())[:4]}"
        initial_state = OrchestrationFlowState(
            flow_id=flow_id, flow_name="full_build_flow", project_id=project_id, commit_sha=commit_sha,
            status="PENDING", current_stage="INITIALIZING", 
            started_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            updated_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            last_event_id_processed=None, dag_id=None, deployment_request_id=None, error_message=None,
            context_data={"user_prompt": user_prompt_for_pipeline, "num_changed_files": len(changed_files_list)}
        )
        await self._update_flow_state(flow_id, initial_state, initial_state_if_new=initial_state) #type: ignore
        logger.info(f"Orchestrator: V0.3 Full Build Flow '{flow_id}' initiated.")
        # The rest of the complex flow from response #77 would go here, with _update_flow_state calls.
        # This involves dispatching to DependencyAgent, then BuildSurfAgent, then PlanAgent, etc.
        # and awaiting their completion events, updating state at each step.
        # This method will become very long if fully pasted here.
        # I'll show the first step integration:
        span = self._start_trace_span_if_available("run_full_build_flow", project_id=project_id, commit_sha=commit_sha, flow_id=flow_id)
        try:
            with span: #type: ignore
                await self._update_flow_state(flow_id, {"status": "RUNNING", "current_stage": "AWAITING_AFFECTED_TASKS"})
                new_commit_event_id = str(uuid.uuid4())
                commit_payload = {
                    "event_id": new_commit_event_id, "project_id": project_id, "commit_sha": commit_sha,
                    "changed_files": [{"file_path": fp, "change_type": "modified"} for fp in changed_files_list],
                    "timestamp": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                }
                await self.message_router.dispatch(logical_message_type="SubmitNewCommit", payload=commit_payload)
                # ... and so on for the rest of the flow from response #77 logic ...
                logger.info(f"Orchestrator: Full build flow '{flow_id}' placeholder completed logic. Further steps would follow.")
                await self._update_flow_state(flow_id, {"status": "COMPLETED_SUCCESS", "current_stage": "FLOW_COMPLETE_PLACEHOLDER"})
                if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))

        except Exception as e:
            logger.error(f"Orchestrator: Error in full build flow '{flow_id}': {e}", exc_info=True)
            await self._update_flow_state(flow_id, {"status": "FAILED", "current_stage": "ORCHESTRATION_ERROR", "error_message": str(e)})
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Full build flow error"))


    # <<< NEW ORCHESTRATED FLOW for V0.3 >>>
    async def orchestrate_code_generation_and_processing(
        self, 
        project_id: str, 
        generation_prompt_text: str, 
        language: Optional[str] = "python",
        existing_code_context: Optional[str] = None,
        max_tokens: int = 2048, # Increased default
        temperature: float = 0.3, # Slightly higher for more creativity
        flow_id_override: Optional[str] = None
        ) -> Optional[str]: # Returns the generated code string or None on failure

        flow_id = flow_id_override or f"codegen_flow_{project_id}_{str(uuid.uuid4())[:8]}"
        span_attrs = {"project_id": project_id, "flow_id": flow_id, "language": language or "python"}
        span = self._start_trace_span_if_available("orchestrate_code_generation", **span_attrs)
        logger.info(f"Orchestrator: Starting Code Generation flow '{flow_id}' for project '{project_id}'.")

        initial_flow_state_dict = {
            "flow_id": flow_id, "flow_name": "code_generation_and_processing", "project_id": project_id, 
            "commit_sha": "N/A_CODEGEN", "status": "RUNNING", "current_stage": "REQUESTING_CODE_GENERATION", 
            "started_at": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            "updated_at": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            "context_data": {"generation_prompt": generation_prompt_text, "language": language}
        }
        initial_flow_state = OrchestrationFlowState(**initial_flow_state_dict) #type: ignore
        await self._update_flow_state(flow_id, initial_flow_state, initial_state_if_new=initial_flow_state) #type: ignore

        generated_code: Optional[str] = None
        try:
            with span: #type: ignore
                if not self.forgeiq_sdk_client or not CodeGenerationPrompt: # Check if SDK and model type were imported
                    err_msg = "ForgeIQ SDK client or CodeGenerationPrompt model not available for code generation."
                    logger.error(f"Flow {flow_id}: {err_msg}")
                    await self._update_flow_state(flow_id, {"status": "FAILED", "error_message": err_msg, "current_stage": "SDK_INIT_FAILURE"})
                    if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, err_msg))
                    raise OrchestrationError(err_msg, flow_id, "SDK_INIT_FAILURE")

                # Step 1: Call ForgeIQ-backend via SDK to generate code
                prompt_details_for_sdk = CodeGenerationPrompt( # Use Pydantic model from backend's api_models for request
                    prompt_text=generation_prompt_text, language=language, project_id=project_id,
                    existing_code_context=existing_code_context, max_tokens=max_tokens, temperature=temperature
                )
                logger.info(f"Flow {flow_id}: Calling SDK to generate code via ForgeIQ-Backend.")

                # The SDK method `generate_code_via_api` expects a dictionary payload matching CodeGenerationRequest
                # from backend's api_models.py. The prompt_details_for_sdk matches CodeGenerationPrompt.
                sdk_request_payload = {
                    "prompt_details": prompt_details_for_sdk.model_dump() # Convert Pydantic to dict
                    # request_id is generated by SDK if not provided
                }

                # This call is to the Python SDK's method, which calls the Python ForgeIQ-Backend API
                code_gen_response_dict = await self.forgeiq_sdk_client.generate_code_via_api(
                    prompt_details=sdk_request_payload["prompt_details"] # type: ignore
                ) 

                # Assuming SDK returns dict compatible with CodeGenerationResponse Pydantic model
                # For stricter typing, cast/validate to CodeGenerationResponse
                sdk_response = CodeGenerationResponse(**code_gen_response_dict) if code_gen_response_dict else None

                if sdk_response and sdk_response.status == "success" and sdk_response.output:
                    generated_code = sdk_response.output.generated_code
                    model_used = sdk_response.output.model_used
                    logger.info(f"Flow {flow_id}: Code generated successfully by model {model_used}.")
                    await self._update_flow_state(flow_id, {
                        "current_stage": "CODE_GENERATED", 
                        "context_data": {
                            **(initial_flow_state.get("context_data") or {}), #type: ignore
                            "generated_code_preview": (generated_code or "")[:200] + "...",
                            "model_used": model_used
                        }
                    })
                    if _tracer: span.set_attribute("codegen.model_used", model_used)
                else:
                    err_msg = sdk_response.error_message if sdk_response else "Code generation failed at backend or SDK error."
                    logger.error(f"Flow {flow_id}: Code generation failed. Reason: {err_msg}")
                    await self._update_flow_state(flow_id, {"status": "FAILED", "current_stage": "CODE_GENERATION_FAILED", "error_message": err_msg})
                    if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, err_msg or "Code generation failed"))
                    raise OrchestrationError(err_msg or "Code generation failed", flow_id, "CODE_GENERATION_FAILED")

                # --- Conceptual Next Steps for the generated code ---
                # For V0.3 Orchestrator, we stop after generation and return the code.
                # The caller of this orchestration method is responsible for further processing.

                logger.info(f"Flow {flow_id}: Code generation successful. Returning generated code.")
                await self._update_flow_state(flow_id, {"status": "COMPLETED_SUCCESS", "current_stage": "CODE_RETURNED"})
                if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                return generated_code

        except OrchestrationError: # Re-raise OrchestrationErrors
            raise
        except Exception as e:
            error_message = f"Orchestrator: Unhandled error in code generation flow '{flow_id}': {e}"
            logger.error(error_message, exc_info=True)
            await self._update_flow_state(flow_id, {"status": "FAILED", "current_stage": "ORCHESTRATION_ERROR", "error_message": str(e)})
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Orchestration flow error"))
            raise OrchestrationError(error_message, flow_id, "ORCHESTRATION_ERROR") from e
        finally:
            if self.forgeiq_sdk_client: # Close SDK client if Orchestrator created it and is done
                # This depends on lifecycle. If SDK client is passed in, don't close it here.
                # For this example, assume Orchestrator might own it if it created it.
                # await self.forgeiq_sdk_client.close() # Be careful with shared clients
                pass


    async def listen_for_orchestration_commands(self):
        logger.info(f"{SERVICE_NAME_ORCHESTRATOR} V0.3 ready (placeholder listener for commands).")
        # Conceptual: Listen for events like "StartFullBuildFlowCommand", "StartCodeGenerationCommand"
        # and then call the respective methods like run_full_build_flow or orchestrate_code_generation_and_processing.
        while True: await asyncio.sleep(3600)


# Example of how Orchestrator might be instantiated and used by a main service entry point
# (e.g., if Orchestrator itself is run as a standalone service)
# This is illustrative; actual service setup would be in its own main script or Docker CMD.
#
# async def run_orchestrator_service():
#     if not os.getenv("REDIS_URL") or not os.getenv("FORGEIQ_API_BASE_URL"):
#         logger.critical("REDIS_URL and FORGEIQ_API_BASE_URL must be set to run Orchestrator service.")
#         return
#
#     # Initialize dependencies
#     event_bus = EventBus()
#     agent_registry = AgentRegistry() # Assumes default in-memory or Redis-backed based on its own config
#     shared_memory = SharedMemoryStore()
#     message_router = MessageRouter(event_bus)
#     
#     sdk_client_instance = None
#     if ForgeIQClient: # Check if SDK was imported
#         try:
#             sdk_client_instance = ForgeIQClient() # Relies on env vars for URL/key
#         except Exception as e:
#             logger.error(f"Failed to init SDK client for Orchestrator service: {e}")
#
#     orchestrator_service = Orchestrator(agent_registry, event_bus, shared_memory, message_router, sdk_client_instance)
#
#     # Example: How a CLI or another service might trigger the code generation flow
#     # This would typically not be in the main_event_loop but called by a trigger.
#     if os.getenv("RUN_ORCHESTRATOR_CODEGEN_EXAMPLE") == "true" and orchestrator_service.forgeiq_sdk_client:
#         logger.info("Orchestrator Service: Running example code generation flow on startup...")
#         asyncio.create_task(orchestrator_service.orchestrate_code_generation_and_processing(
#             project_id="example_project",
#             generation_prompt_text="Create a Python function that calculates factorial.",
#             language="python"
#         ))
#
#     await orchestrator_service.listen_for_orchestration_commands() # Start main listener
#
# if __name__ == "__main__":
#     # This main block makes the orchestrator runnable as a service that listens for commands.
#     # It requires all dependencies like Redis and ForgeIQ Backend (for SDK) to be available.
#     # To run example on start: export RUN_ORCHESTRATOR_CODEGEN_EXAMPLE=true
#     # And ensure FORGEIQ_API_BASE_URL and CODE_GENERATION_LLM_API_KEY (for backend) are set.
#     
#     # The main_async_runner for Orchestrator as a service:
#     # if not (os.getenv("REDIS_URL") and os.getenv("FORGEIQ_API_BASE_URL")):
#     #    print("REDIS_URL and FORGEIQ_API_BASE_URL must be set.", file=sys.stderr)
#     # else:
#     #    asyncio.run(run_orchestrator_service())
#     logger.info("Orchestrator module loaded. Can be run as a service or its methods called.")
# In Orchestrator class (core/orchestrator/main_orchestrator.py)
async def request_mcp_strategy_optimization(self, 
                                          project_id: str, 
                                          current_dag: Optional[DagDefinition] = None
                                         ) -> Optional[Dict[str, Any]]: # Or specific MCPStrategyResponse model
    if not self.forgeiq_sdk_client:
        logger.error(f"Orchestrator: SDK client not available to request MCP strategy for '{project_id}'.")
        return None

    span = self._start_trace_span_if_available("request_mcp_strategy", project_id=project_id)
    logger.info(f"Orchestrator: Requesting MCP build strategy optimization for project '{project_id}'.")
    try:
        with span: #type: ignore
            # Convert current_dag (TypedDict) to dict for SDK if needed, or SDK handles TypedDicts
            dag_info_payload = current_dag if current_dag else None # Or a summary

            # Create the SDKAlgorithmContext-like structure if the SDK method reuses it
            # Or, if SDK has a new context type for this, use that.
            # For now, assuming a simple dict payload for the SDK call.
            sdk_context_for_mcp = { # This should match what SDK's request_mcp_build_strategy expects
                "project_id": project_id,
                "dag_representation": current_dag.get("nodes") if current_dag else None, # Example
                "telemetry_data": {"source": "Orchestrator_MCP_Request"}
            }

            # This calls the new SDK method, which calls the new ForgeIQ-backend endpoint, 
            # which then calls your private MCP API.
            # Assuming the SDK method request_mcp_build_strategy expects project_id and current_dag_info
            mcp_response = await self.forgeiq_sdk_client.request_mcp_build_strategy(
                project_id=project_id,
                current_dag_info=dag_info_payload # Pass the DAG info if needed by MCP
            )

            if mcp_response:
                logger.info(f"Orchestrator: Received strategy from MCP for '{project_id}': {message_summary(mcp_response)}")
                # TODO: Orchestrator would then process this strategy
                # (e.g., use it to create/modify a DAG for PlanAgent)
                if _trace_api and span: span.set_attribute("mcp.response_status", mcp_response.get("status")); span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                return mcp_response
            else:
                logger.warning(f"Orchestrator: No strategy response from MCP for '{project_id}'.")
                if _trace_api and span: span.set_attribute("mcp.response_received", False)
                return None
    except Exception as e:
        logger.error(f"Orchestrator: Error requesting MCP strategy for '{project_id}': {e}", exc_info=True)
        if _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR))
        return None
# In Orchestrator class (core/orchestrator/main_orchestrator.py)
# ... (ensure SDKMCPStrategyRequestContext, SDKMCPStrategyResponse are imported from sdk.models)
from sdk.models import SDKMCPStrategyRequestContext, SDKMCPStrategyResponse # Add these

# ... (within __init__ or wherever sdk client is available)
# self.forgeiq_sdk_client: Optional[ForgeIQClient] = forgeiq_sdk_client (from constructor)

async def request_and_apply_mcp_optimization(
    self, 
    project_id: str, 
    current_dag: DagDefinition, # The current DAG to be optimized
    flow_id: str # Parent flow ID for tracing and state
    ) -> Optional[DagDefinition]: # Returns a new DAG if optimization occurred
    """
    Requests MCP to optimize a DAG and returns the new DAG if successful.
    """
    if not self.forgeiq_sdk_client:
        logger.error(f"Flow {flow_id}: SDK client not available for MCP optimization request for project '{project_id}'.")
        return None

    span_attrs = {"project_id": project_id, "flow_id": flow_id, "mcp_task": "optimize_dag"}
    span = self._start_trace_span_if_available("request_mcp_optimization", **span_attrs)
    logger.info(f"Flow {flow_id}: Orchestrator requesting MCP build strategy optimization for project '{project_id}'.")

    await self._update_flow_state(flow_id, {"current_stage": f"REQUESTING_MCP_OPTIMIZATION_FOR_DAG_{current_dag['dag_id']}"})

    try:
        with span: #type: ignore
            # Prepare context for the SDK call, which then goes to ForgeIQ-backend, then to private MCP
            # The SDKMCPStrategyRequestContext requires project_id, dag_representation, telemetry_data
            mcp_request_context = SDKMCPStrategyRequestContext(
                project_id=project_id,
                # Convert current_dag (TypedDict) to a list of dicts for dag_representation
                current_dag_snapshot=[dict(node) for node in current_dag.get("nodes", [])], 
                optimization_goal="general_build_efficiency", # Example goal
                additional_mcp_context={"triggering_flow_id": flow_id}
            )

            mcp_response: SDKMCPStrategyResponse = await self.forgeiq_sdk_client.request_mcp_build_strategy(
                context=mcp_request_context # type: ignore
            )

            if mcp_response and mcp_response.get("status") == "strategy_provided" and mcp_response.get("strategy_details"): # Example success status from MCP
                strategy_details = mcp_response["strategy_details"]
                new_dag_raw = strategy_details.get("new_dag_definition_raw") # This is Dict[str,Any] from API model

                if new_dag_raw and isinstance(new_dag_raw, dict):
                    # Validate/cast new_dag_raw to DagDefinition TypedDict
                    # For simplicity, direct cast. Pydantic would be safer for validation.
                    validated_new_dag = DagDefinition(**new_dag_raw) # type: ignore
                    logger.info(f"Flow {flow_id}: MCP provided optimized DAG '{validated_new_dag.get('dag_id')}' for project '{project_id}'.")
                    if _trace_api and span: 
                        span.set_attribute("mcp.strategy_id", strategy_details.get("strategy_id"))
                        span.set_attribute("mcp.new_dag_id", validated_new_dag.get("dag_id"))
                        span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                    await self._update_flow_state(flow_id, {"current_stage": f"MCP_OPTIMIZATION_RECEIVED_DAG_{validated_new_dag.get('dag_id')}"})
                    return validated_new_dag
                else:
                    logger.info(f"Flow {flow_id}: MCP provided strategy but no new DAG definition for project '{project_id}'. Directives: {strategy_details.get('directives')}")
                    if _trace_api and span: span.set_attribute("mcp.directives_only", True)
                    # If only directives, the Orchestrator would need to interpret and act on them.
            else:
                msg = f"MCP strategy optimization failed or no strategy provided for project '{project_id}'. Response: {mcp_response}"
                logger.warning(f"Flow {flow_id}: {msg}")
                if _trace_api and span: span.set_attribute("mcp.error", msg)

            await self._update_flow_state(flow_id, {"current_stage": f"MCP_OPTIMIZATION_NO_NEW_DAG"})
    except Exception as e:
        logger.error(f"Flow {flow_id}: Error requesting/processing MCP strategy for project '{project_id}': {e}", exc_info=True)
        await self._update_flow_state(flow_id, {"current_stage": "MCP_OPTIMIZATION_ERROR", "error_message": str(e)})
        if _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR))
    return None

# You would then call this `request_mcp_strategy_optimization` method from within
# a larger flow like `run_full_build_flow` at an appropriate point, e.g., after
# `DependencyAgent` determines affected tasks but before `PlanAgent` executes a DAG.
# If an optimized DAG is returned, the Orchestrator would then pass *that* DAG to PlanAgent.
