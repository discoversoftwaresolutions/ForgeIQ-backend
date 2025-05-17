# ==============================================
# 📁 core/orchestrator/main_orchestrator.py (V0.3)
# ==============================================
import os
import json
import asyncio
import logging
import uuid
import datetime
import time # For _wait_for_event
from typing import Dict, Any, Optional, List

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
except ImportError:
    logging.getLogger(SERVICE_NAME_ORCHESTRATOR).warning("Orchestrator: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from core.agent_registry import AgentRegistry
from core.shared_memory import SharedMemoryStore
from core.message_router import MessageRouter, MessageRouteNotFoundError, InvalidMessagePayloadError

from interfaces.types.events import (
    FileChange, NewCommitEvent, # For run_full_build_flow
    PipelineGenerationRequestEvent, PipelineGenerationUserPrompt, # For BuildSurf via MessageRouter
    DagDefinition, DagDefinitionCreatedEvent, DagExecutionStatusEvent, # For PlanAgent interaction
    DeploymentRequestEvent, DeploymentStatusEvent, # For CI_CD_Agent via MessageRouter
    AffectedTasksIdentifiedEvent, SecurityScanResultEvent
)
from interfaces.types.orchestration import OrchestrationFlowState

# Import SDK client and relevant request/response models for code generation
try:
    from sdk.client import ForgeIQClient
    # Assuming CodeGenerationPrompt is a Pydantic model in ForgeIQ-backend's api_models
    # and SDK might use TypedDicts or its own Pydantic models.
    # For this example, we'll use the structure from ForgeIQ-backend's api_models.py
    # These imports are for type hinting what the Orchestrator expects to pass to SDK
    from apps.forgeiq_backend.app.api_models import CodeGenerationPrompt, CodeGenerationResponse, GeneratedCodeOutput
except ImportError as e_sdk:
    logger.error(f"Orchestrator: Failed to import ForgeIQ SDK or its models: {e_sdk}. Code generation flow will be impacted.", exc_info=True)
    ForgeIQClient = None # type: ignore
    CodeGenerationPrompt = None # type: ignore
    CodeGenerationResponse = None # type: ignore

DEFAULT_EVENT_TIMEOUT = int(os.getenv("ORCHESTRATOR_EVENT_TIMEOUT_SECONDS", "300")) # 5 minutes
FLOW_STATE_EXPIRY_SECONDS = int(os.getenv("ORCHESTRATOR_FLOW_STATE_EXPIRY_SECONDS", 24 * 60 * 60)) # 1 day

class OrchestrationError(Exception):
    """Custom exception for orchestration failures."""
    def __init__(self, message: str, flow_id: Optional[str] = None, stage: Optional[str] = None):
        super().__init__(message)
        self.flow_id = flow_id
        self.stage = stage
        self.message = message

    def __str__(self):
        return f"OrchestrationError (Flow: {self.flow_id}, Stage: {self.stage}): {self.message}"


class Orchestrator:
    def __init__(self, 
                 agent_registry: AgentRegistry, 
                 event_bus: EventBus, 
                 shared_memory: SharedMemoryStore,
                 message_router: MessageRouter,
                 forgeiq_sdk_client: Optional[ForgeIQClient] = None # Pass initialized client
                ):
        logger.info("Initializing Orchestrator (V0.3 with Code Generation Flow)...")
        self.agent_registry = agent_registry
        self.event_bus = event_bus 
        self.shared_memory = shared_memory
        self.message_router = message_router
        self.forgeiq_sdk_client = forgeiq_sdk_client # Store the SDK client

        if not all([self.event_bus.redis_client, 
                    self.shared_memory.redis_client, 
                    self.message_router,
                    self.agent_registry]): # Added agent_registry check
             logger.error("Orchestrator critical: One or more core services are not properly initialized.")

        if not self.forgeiq_sdk_client and ForgeIQClient is not None: # Check if class was imported
            logger.warning("Orchestrator: ForgeIQ SDK client not provided during initialization. Code generation flow will fail if attempted.")
        elif ForgeIQClient is None:
            logger.error("Orchestrator: ForgeIQClient class could not be imported. Code generation flow is unavailable.")

        logger.info("Orchestrator V0.3 Initialized.")

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same helper as before) ...
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
