
import os
import json
import asyncio
import logging
import uuid
import datetime
import time
import hashlib
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
    _tracer = otel_trace_api.get_tracer(__name__) 
    _trace_api = otel_trace_api
except ImportError:
    logging.getLogger(__name__).info("OpenTelemetry API not found for Orchestrator.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus
from core.agent_registry import AgentRegistry
from core.shared_memory import SharedMemoryStore
from core.message_router import MessageRouter, MessageRouteNotFoundError, InvalidMessagePayloadError
from interfaces.types.events import (
    FileChange, NewCommitEvent,
    PipelineGenerationRequestEvent, PipelineGenerationUserPrompt,
    DagDefinition, DagDefinitionCreatedEvent, DagExecutionStatusEvent,
    DeploymentRequestEvent, DeploymentStatusEvent, AffectedTasksIdentifiedEvent, SecurityScanResultEvent
)
from interfaces.types.orchestration import OrchestrationFlowState # <<< NEW IMPORT

DEFAULT_EVENT_TIMEOUT = 300 # 5 minutes
FLOW_STATE_EXPIRY_SECONDS = 24 * 60 * 60 # Store flow state for 1 day

class Orchestrator:
    def __init__(self, 
                 agent_registry: AgentRegistry, 
                 event_bus: EventBus, 
                 shared_memory: SharedMemoryStore, # <<< Now used for state
                 message_router: MessageRouter
                ):
        logger.info("Initializing Orchestrator (V0.2 with state persistence)...")
        self.agent_registry = agent_registry
        self.event_bus = event_bus 
        self.shared_memory = shared_memory # <<< Store instance
        self.message_router = message_router

        if not all([self.event_bus.redis_client, self.shared_memory.redis_client, self.message_router]):
             logger.error("Orchestrator critical: One or more core services (EventBus, SharedMemory, MessageRouter) not connected/provided.")
        logger.info("Orchestrator V0.2 Initialized.")

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same as before from response #61)
        if _tracer and _trace_api:
            span = _tracer.start_span(f"orchestrator.{operation_name}", context=parent_context)
            for k, v in attrs.items(): span.set_attribute(k, v)
            return span
        class NoOpSpan:
            def __enter__(self): return self; 
            def __exit__(self,tp,vl,tb): pass; 
            def set_attribute(self,k,v): pass; 
            def record_exception(self,e,attributes=None): pass; 
            def set_status(self,s): pass;
            def end(self): pass
        return NoOpSpan()

    async def _update_flow_state(self, flow_id: str, updates: Dict[str, Any]):
        if not self.shared_memory.redis_client:
            logger.warning(f"Flow {flow_id}: Cannot update state, SharedMemoryStore not connected.")
            return

        state_key = f"orchestration_flow:{flow_id}"
        current_state_dict = await self.shared_memory.get_value(state_key, expected_type=dict)
        current_state: OrchestrationFlowState = OrchestrationFlowState(**current_state_dict) if current_state_dict else {} # type: ignore

        # Merge updates
        current_state.update(updates) # type: ignore 
        current_state["updated_at"] = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"

        await self.shared_memory.set_value(state_key, current_state, expiry_seconds=FLOW_STATE_EXPIRY_SECONDS)
        logger.debug(f"Flow {flow_id}: State updated - Stage: {current_state.get('current_stage')}, Status: {current_state.get('status')}")

    async def _wait_for_event(self, # ... (This method remains the same as in response #61) ...
                              expected_event_type: str,
                              correlation_id: str,
                              correlation_id_field_in_event: str,
                              event_channel_pattern: str,
                              timeout_seconds: int = DEFAULT_EVENT_TIMEOUT
                             ) -> Optional[Dict[str, Any]]:
        # ... (implementation from response #61)
        if not self.event_bus.redis_client: return None
        pubsub = None; span = self._start_trace_span_if_available("wait_for_event", event_type=expected_event_type, correlation_id=correlation_id)
        try:
            with span: # type: ignore
                pubsub = self.event_bus.subscribe_to_channel(event_channel_pattern)
                if not pubsub: logger.error(f"Failed to subscribe to '{event_channel_pattern}'"); return None
                logger.info(f"Orch waiting for '{expected_event_type}' with {correlation_id_field_in_event}='{correlation_id}' on '{event_channel_pattern}'")
                start_wait_time = time.monotonic()
                while True:
                    if time.monotonic() - start_wait_time > timeout_seconds: logger.warning(f"Timeout waiting for '{expected_event_type}'."); return None
                    message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                    if message and message["type"] == "pmessage":
                        try:
                            event_data = json.loads(message["data"].decode('utf-8'))
                            if event_data.get("event_type") == expected_event_type and event_data.get(correlation_id_field_in_event) == correlation_id:
                                logger.info(f"Orch received '{expected_event_type}': {message_summary(event_data)}")
                                return event_data
                        except Exception as e: logger.error(f"Orch error processing message: {e}")
                    await asyncio.sleep(0.2)
        finally:
            if pubsub:
                try:
                    if hasattr(pubsub, 'punsubscribe'): await asyncio.to_thread(pubsub.punsubscribe, event_channel_pattern)
                    if hasattr(pubsub, 'close'): await asyncio.to_thread(pubsub.close)
                except Exception as e_close: logger.error(f"Error closing pubsub: {e_close}")
        return None


    async def run_full_build_flow(self, project_id: str, commit_sha: str, 
                                  changed_files_list: List[str], 
                                  user_prompt_for_pipeline: Optional[str] = None,
                                  flow_id: Optional[str] = None): # Allow providing a flow_id for resumption/tracking

        flow_id = flow_id or f"flow_{project_id}_{commit_sha[:7]}_{str(uuid.uuid4())[:8]}"
        span = self._start_trace_span_if_available("run_full_build_flow", project_id=project_id, commit_sha=commit_sha, flow_id=flow_id)
        logger.info(f"Orchestrator: Starting full build flow '{flow_id}' for project '{project_id}', commit '{commit_sha}'.")

        # Initial state
        initial_flow_state = OrchestrationFlowState(
            flow_id=flow_id, flow_name="full_build_flow", project_id=project_id, commit_sha=commit_sha,
            status="RUNNING", current_stage="INITIALIZING", 
            started_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            updated_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            last_event_id_processed=None, dag_id=None, deployment_request_id=None, error_message=None,
            context_data={"user_prompt": user_prompt_for_pipeline}
        )
        await self._update_flow_state(flow_id, initial_flow_state) # type: ignore

        try:
            with span: #type: ignore
                # Step 1: Trigger DependencyAgent via MessageRouter
                await self._update_flow_state(flow_id, {"current_stage": "AWAITING_AFFECTED_TASKS"})
                logger.info(f"Flow {flow_id}: Dispatching 'SubmitNewCommit'.")
                # ... (Dispatch SubmitNewCommit as in response #61, using self.message_router.dispatch) ...
                new_commit_event_id = str(uuid.uuid4())
                commit_payload = {
                    "event_id": new_commit_event_id, "project_id": project_id, "commit_sha": commit_sha,
                    "changed_files": [{"file_path": fp, "change_type": "modified"} for fp in changed_files_list],
                    "timestamp": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                }
                await self.message_router.dispatch(logical_message_type="SubmitNewCommit", payload=commit_payload)

                affected_tasks_event = await self._wait_for_event(
                    expected_event_type="AffectedTasksIdentifiedEvent", correlation_id=new_commit_event_id,
                    correlation_id_field_in_event="triggering_event_id",
                    event_channel_pattern=f"events.project.{project_id}.affected_tasks.identified"
                )
                if not affected_tasks_event:
                    await self._update_flow_state(flow_id, {"status": "FAILED", "current_stage": "AFFECTED_TASKS_TIMEOUT", "error_message": "Timeout waiting for AffectedTasksEvent"})
                    if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "No AffectedTasksEvent"))
                    return
                await self._update_flow_state(flow_id, {"last_event_id_processed": affected_tasks_event.get("triggering_event_id")})


                # Step 2: (Optional) DAG generation via BuildSurfAgent
                # ... (Logic from response #61 for BuildSurfAgent interaction using MessageRouter and _wait_for_event) ...
                # ... (Remember to call _update_flow_state at each significant step/change) ...
                dag_to_execute: Optional[DagDefinition] = None
                if user_prompt_for_pipeline:
                    await self._update_flow_state(flow_id, {"current_stage": "AWAITING_DAG_GENERATION"})
                    # ... (dispatch "GeneratePipelineFromPrompt" as before) ...
                    pipeline_req_id = str(uuid.uuid4())
                    pipeline_payload = { "request_id": pipeline_req_id, "user_prompt_text": user_prompt_for_pipeline, "target_project_id": project_id}
                    await self.message_router.dispatch(logical_message_type="GeneratePipelineFromPrompt", payload=pipeline_payload)
                    dag_created_event = await self._wait_for_event(expected_event_type="DagDefinitionCreatedEvent", correlation_id=pipeline_req_id, ...) # Full params
                    if dag_created_event and "dag" in dag_created_event: dag_to_execute = dag_created_event["dag"] # type: ignore
                    else: # Handle failure
                        await self._update_flow_state(flow_id, {"status": "FAILED", "current_stage": "DAG_GENERATION_FAILED", "error_message": "Failed to get DAG from BuildSurfAgent"})
                        return
                else: # Construct default DAG
                    affected_tasks_list = affected_tasks_event.get("affected_tasks", [])
                    if affected_tasks_list:
                        # ... construct default dag_to_execute ... (as in response #61)
                        dag_nodes: List[DagNode] = [{"id": f"{task_type}_{i}", "task_type": task_type, "dependencies": []} for i, task_type in enumerate(affected_tasks_list)]
                        if dag_nodes:
                            dag_to_execute = DagDefinition(dag_id=f"default_dag_{flow_id[:8]}", project_id=project_id, nodes=dag_nodes) #type: ignore
                    if not dag_to_execute: logger.info(f"Flow {flow_id}: No tasks affected, no user prompt, no DAG to execute.")


                # Step 3: Trigger PlanAgent
                if dag_to_execute:
                    await self._update_flow_state(flow_id, {"current_stage": "AWAITING_DAG_EXECUTION", "dag_id": dag_to_execute["dag_id"]})
                    # ... (Publish DagDefinitionCreatedEvent for PlanAgent as before, or ensure BuildSurf did) ...
                    # ... (Then _wait_for_event for DagExecutionStatusEvent) ...
                    if not user_prompt_for_pipeline: # If we constructed it, publish it
                        plan_agent_trigger_event_id = str(uuid.uuid4())
                        dag_event_for_plan_agent = DagDefinitionCreatedEvent(
                            event_type="DagDefinitionCreatedEvent", request_id=plan_agent_trigger_event_id,
                            project_id=project_id, dag=dag_to_execute, raw_llm_response= None,
                            timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                        )
                        plan_agent_dag_channel = f"events.project.{project_id or 'global'}.dag.created"
                        await self.message_router.event_bus.publish(plan_agent_dag_channel, dag_event_for_plan_agent) # Direct publish

                    dag_exec_status = await self._wait_for_event(expected_event_type="DagExecutionStatusEvent", correlation_id=dag_to_execute["dag_id"], ...) # Full params
                    if not (dag_exec_status and dag_exec_status.get("status") == "COMPLETED_SUCCESS"):
                         final_dag_status = dag_exec_status.get("status") if dag_exec_status else "TIMEOUT"
                         await self._update_flow_state(flow_id, {"status": "FAILED", "current_stage": "DAG_EXECUTION_FAILED", "error_message": f"DAG failed: {final_dag_status}"})
                         return
                    await self._update_flow_state(flow_id, {"current_stage": "DAG_EXECUTION_COMPLETED", "last_event_id_processed": dag_exec_status.get("dag_id")})


                # Step 4: SecurityAgent Scan
                # ... (Wait for SecurityScanResultEvent as before) ...
                # ... (_update_flow_state with stage and findings summary) ...

                # Step 5: CI_CD_Agent Deploy
                await self._update_flow_state(flow_id, {"current_stage": "AWAITING_DEPLOYMENT_STATUS"})
                # ... (Dispatch RequestServiceDeployment via MessageRouter as before) ...
                deploy_req_id = str(uuid.uuid4())
                service_to_deploy = project_id # Simplified, needs proper mapping
                deployment_payload = { "request_id": deploy_req_id, "project_id": project_id, "service_name": service_to_deploy, "commit_sha": commit_sha, "target_environment": "staging"}
                await self.message_router.dispatch(logical_message_type="RequestServiceDeployment", payload=deployment_payload)

                deployment_status = await self._wait_for_event(expected_event_type="DeploymentStatusEvent", correlation_id=deploy_req_id, ...) # Full params

                final_flow_status = "COMPLETED_SUCCESS"
                final_stage = "DEPLOYMENT_COMPLETED"
                error_msg = None
                if not (deployment_status and deployment_status.get("status") == "SUCCESSFUL"):
                    final_flow_status = "FAILED"
                    final_stage = "DEPLOYMENT_FAILED"
                    error_msg = f"Deployment status: {deployment_status.get('status') if deployment_status else 'TIMEOUT'}"
                    logger.error(f"Flow {flow_id}: {error_msg}")

                await self._update_flow_state(flow_id, {"status": final_flow_status, "current_stage": final_stage, "deployment_request_id": deploy_req_id, "error_message": error_msg})
                logger.info(f"Orchestrator: Full build flow '{flow_id}' completed with status: {final_flow_status}.")
                if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK if final_flow_status == "COMPLETED_SUCCESS" else _trace_api.StatusCode.ERROR))

            except Exception as e: # Catch-all for the main flow logic
                logger.error(f"Orchestrator: Unhandled error in full build flow '{flow_id}': {e}", exc_info=True)
                await self._update_flow_state(flow_id, {"status": "FAILED", "current_stage": "ORCHESTRATION_ERROR", "error_message": str(e)})
                if _tracer and _trace_api: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Orchestration flow error"))

    async def listen_for_orchestration_commands(self):
         logger.info(f"{SERVICE_NAME_ORCHESTRATOR} ready to orchestrate (V0.2: placeholder listener).")
         # Conceptual: listen for "StartFullBuildFlowCommand" on event bus
         # For now, keeps the process alive if run as a service.
         while True: await asyncio.sleep(3600)


# --- Example Usage Block (for testing/demonstration if run directly) ---
async def run_orchestration_example_flow_v2():
    # ... (Same setup as before for initializing components - Orchestrator now takes MessageRouter) ...
    if not os.getenv("REDIS_URL"): print("REDIS_URL not set for example.", file=sys.stderr); return
    if not logging.getLogger().hasHandlers(): logging.basicConfig(level=LOG_LEVEL_ORCHESTRATOR)
    logger.info("--- Orchestrator V0.2 Example: Initializing Dependencies ---")
    try:
        eb = EventBus(); ar = AgentRegistry(); sm = SharedMemoryStore(); mr = MessageRouter(eb)
        if not eb.redis_client or not sm.redis_client : logger.error("Example: Redis not connected for core services."); return
        orchestrator = Orchestrator(ar, eb, sm, mr)
    except Exception as e: logger.error(f"Failed to initialize for example: {e}", exc_info=True); return

    project_id = "project_omega"
    commit = hashlib.sha1(os.urandom(16)).hexdigest()[:10]
    files = [f"src/file{i}.py" for i in range(3)]
    prompt = "Standard Python CI: lint with flake8, test with pytest, build a wheel, deploy to staging."

    logger.info(f"--- Starting Orchestrator V0.2 Example Flow for Project: {project_id} ---")
    try:
        await orchestrator.run_full_build_flow(project_id, commit, files, user_prompt_for_pipeline=prompt)
    except Exception as e: logger.error(f"Error during example orchestration: {e}", exc_info=True)
    finally: logger.info(f"--- Orchestrator V0.2 Example Flow for Project: {project_id} Finished ---")


if __name__ == "__main__":
    import sys # For example stderr print
    if os.getenv("RUN_ORCHESTRATOR_EXAMPLE") == "true":
        logger.info("Attempting to run Orchestrator V0.2 example flow...")
        # ... (rest of __main__ for example from response #61) ...
        if not REDIS_URL_SHARED_MEM: print("CRITICAL: REDIS_URL env var must be set.", file=sys.stderr)
        else:
            try: asyncio.run(run_orchestration_example_flow_v2())
            except KeyboardInterrupt: logger.info("Orchestrator example V0.2 interrupted.")
            except Exception as e: logger.critical(f"Orchestrator example V0.2 failed: {e}", exc_info=True)
    else:
        logger.info("Orchestrator module loaded. RUN_ORCHESTRATOR_EXAMPLE=true to run example.")
        # To run as a service:
        # async def start_service():
        #     if not REDIS_URL_SHARED_MEM: return
        #     eb=EventBus(); ar=AgentRegistry(); sm=SharedMemoryStore(); mr=MessageRouter(eb)
        #     orc = Orchestrator(ar,eb,sm,mr)
        #     await orc.listen_for_orchestration_commands()
        # if REDIS_URL_SHARED_MEM: asyncio.run(start_service())
