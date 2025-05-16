# ==============================================
# 📁 core/orchestrator/main_orchestrator.py
# ==============================================
import os
import json
import asyncio
import logging
import uuid
import datetime
import time
import hashlib # For the example usage block
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
    logging.getLogger(__name__).info("OpenTelemetry API not found. Orchestrator will operate without distributed tracing.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from core.agent_registry import AgentRegistry # AgentRegistrationInfo, AgentEndpoint not directly used by Orchestrator logic here
from core.shared_memory import SharedMemoryStore
from core.message_router import MessageRouter, MessageRouteNotFoundError, InvalidMessagePayloadError # <<< NEW IMPORT
from interfaces.types.events import (
    FileChange, # Used in constructing payload for MessageRouter
    DagDefinition, # Used in type hinting for dag_to_execute
    DagDefinitionCreatedEvent, DagExecutionStatusEvent, 
    SecurityScanResultEvent, DeploymentStatusEvent, AffectedTasksIdentifiedEvent # For _wait_for_event
)

DEFAULT_EVENT_TIMEOUT = 300

class Orchestrator:
    def __init__(self, 
                 agent_registry: AgentRegistry, 
                 event_bus: EventBus, # Still needed for _wait_for_event
                 shared_memory: SharedMemoryStore,
                 message_router: MessageRouter # <<< NEW PARAMETER
                ):
        logger.info("Initializing Orchestrator...")
        self.agent_registry = agent_registry
        self.event_bus = event_bus 
        self.shared_memory = shared_memory
        self.message_router = message_router # <<< STORED INSTANCE

        if not self.event_bus.redis_client:
             logger.error("Orchestrator: EventBus not connected.")
        if not self.message_router: # Or check if its internal event_bus is connected
             logger.error("Orchestrator: MessageRouter not provided or not properly initialized.")
        # ... other checks ...
        logger.info("Orchestrator Initialized with MessageRouter.")

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same as before)
        if _tracer and _trace_api:
            span = _tracer.start_span(f"orchestrator.{operation_name}", context=parent_context)
            for k, v in attrs.items(): span.set_attribute(k, v)
            return span
        class NoOpSpan:
            def __enter__(self): return self
            def __exit__(self,tp,vl,tb): pass
            def set_attribute(self,k,v): pass
            def record_exception(self,e,attributes=None): pass
            def set_status(self,s): pass
            def end(self): pass
        return NoOpSpan()

    async def _wait_for_event(self, # ... (This method remains the same as in response #57) ...
                              expected_event_type: str,
                              correlation_id: str,
                              correlation_id_field_in_event: str,
                              event_channel_pattern: str,
                              timeout_seconds: int = DEFAULT_EVENT_TIMEOUT
                             ) -> Optional[Dict[str, Any]]:
        if not self.event_bus.redis_client:
            logger.error(f"Cannot wait for event '{expected_event_type}': EventBus not connected.")
            return None
        # ... (rest of the _wait_for_event logic from response #57) ...
        pubsub = None 
        try:
            # ... (subscription logic) ...
            pubsub = self.event_bus.subscribe_to_channel(event_channel_pattern)
            if not pubsub:
                logger.error(f"Failed to subscribe to pattern '{event_channel_pattern}' for Orchestrator.")
                return None
            logger.info(f"Orchestrator waiting for event '{expected_event_type}' with {correlation_id_field_in_event}='{correlation_id}' on pattern '{event_channel_pattern}'")
            start_wait_time = time.monotonic()
            while True:
                if time.monotonic() - start_wait_time > timeout_seconds:
                    logger.warning(f"Timeout waiting for event '{expected_event_type}' for '{correlation_id}'.")
                    return None
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "pmessage":
                    try:
                        event_data = json.loads(message["data"].decode('utf-8'))
                        if event_data.get("event_type") == expected_event_type and \
                           event_data.get(correlation_id_field_in_event) == correlation_id:
                            logger.info(f"Orchestrator received expected event '{expected_event_type}': {message_summary(event_data)}")
                            return event_data
                    except json.JSONDecodeError: logger.error(f"Orchestrator: Could not decode JSON: {message['data'][:100]}")
                    except Exception as e: logger.error(f"Orchestrator: Error processing message: {e}")
                await asyncio.sleep(0.2)
        finally:
            if pubsub:
                try:
                    if hasattr(pubsub, 'punsubscribe'): await asyncio.to_thread(pubsub.punsubscribe, event_channel_pattern)
                    if hasattr(pubsub, 'close'): await asyncio.to_thread(pubsub.close)
                except Exception as e_close: logger.error(f"Error closing pubsub in _wait_for_event: {e_close}")
        return None


    async def run_full_build_flow(self, project_id: str, commit_sha: str, 
                                  changed_files_list: List[str], 
                                  user_prompt_for_pipeline: Optional[str] = None):
        request_id = str(uuid.uuid4())
        span = self._start_trace_span_if_available("run_full_build_flow", 
                                                   project_id=project_id, commit_sha=commit_sha, 
                                                   request_id=request_id, has_user_prompt=bool(user_prompt_for_pipeline))
        logger.info(f"Orchestrator: Starting full build flow for project '{project_id}', commit '{commit_sha}', request_id '{request_id}'")

        try:
            with span: #type: ignore
                # Step 1: Trigger DependencyAgent via MessageRouter
                logger.info(f"Orchestrator: Dispatching 'SubmitNewCommit' for project '{project_id}'.")
                new_commit_event_id = str(uuid.uuid4()) # This will be part of the payload, router makes actual event_id
                commit_payload = {
                    "event_id": new_commit_event_id, # For correlation
                    "project_id": project_id,
                    "commit_sha": commit_sha,
                    "changed_files": [{"file_path": fp, "change_type": "modified"} for fp in changed_files_list],
                    "timestamp": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                    # Add author, message if available
                }
                try:
                    await self.message_router.dispatch(logical_message_type="SubmitNewCommit", payload=commit_payload)
                except (MessageRouteNotFoundError, InvalidMessagePayloadError) as e_dispatch:
                    logger.error(f"Orchestrator: Failed to dispatch SubmitNewCommit: {e_dispatch}")
                    if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"Dispatch failed: {e_dispatch}"))
                    return

                affected_tasks_event_data = await self._wait_for_event(
                    expected_event_type="AffectedTasksIdentifiedEvent",
                    correlation_id=new_commit_event_id, # Correlate with the event_id we put in payload
                    correlation_id_field_in_event="triggering_event_id",
                    event_channel_pattern=f"events.project.{project_id}.affected_tasks.identified"
                )

                if not affected_tasks_event_data:
                    logger.error("Orchestrator: Did not receive AffectedTasksIdentifiedEvent. Halting flow.")
                    if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "No AffectedTasksIdentifiedEvent"))
                    return
                # ... (rest of the logic for affected_tasks as before) ...
                affected_tasks: List[str] = affected_tasks_event_data.get("affected_tasks", [])
                logger.info(f"Orchestrator: Affected tasks identified: {affected_tasks}")


                # Step 2: (Optional) DAG generation via BuildSurfAgent using MessageRouter
                dag_to_execute: Optional[DagDefinition] = None
                if user_prompt_for_pipeline:
                    logger.info(f"Orchestrator: Dispatching 'GeneratePipelineFromPrompt'.")
                    pipeline_req_id = str(uuid.uuid4())
                    pipeline_payload = {
                        "request_id": pipeline_req_id,
                        "user_prompt_text": user_prompt_for_pipeline,
                        "target_project_id": project_id,
                        "requested_by": SERVICE_NAME_ORCHESTRATOR
                    }
                    try:
                        await self.message_router.dispatch(logical_message_type="GeneratePipelineFromPrompt", payload=pipeline_payload)
                    except (MessageRouteNotFoundError, InvalidMessagePayloadError) as e_dispatch:
                        logger.error(f"Orchestrator: Failed to dispatch GeneratePipelineFromPrompt: {e_dispatch}")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"Dispatch failed: {e_dispatch}"))
                        return # Halt if dynamic DAG was essential

                    dag_created_event_data = await self._wait_for_event(
                        expected_event_type="DagDefinitionCreatedEvent",
                        correlation_id=pipeline_req_id,
                        correlation_id_field_in_event="request_id",
                        event_channel_pattern=f"events.project.{project_id or 'global'}.dag.created"
                    )
                    if dag_created_event_data and "dag" in dag_created_event_data:
                        dag_to_execute = dag_created_event_data["dag"]
                        logger.info(f"Orchestrator: DAG {dag_to_execute['dag_id']} received via event (triggered by BuildSurfAgent).")
                    else:
                        logger.error(f"Orchestrator: Failed to get DAG event from BuildSurfAgent for request {pipeline_req_id}.")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "DAG generation failed"))
                        return
                else:
                    # ... (logic for constructing default DAG if no user_prompt, as before) ...
                    if affected_tasks:
                        dag_nodes: List[DagNode] = [{"id": f"{task_type}_{i}", "task_type": task_type, "dependencies": []} for i, task_type in enumerate(affected_tasks)]
                        if dag_nodes:
                            dag_to_execute = DagDefinition(
                                dag_id=f"default_dag_{project_id}_{commit_sha[:7]}_{str(uuid.uuid4())[:4]}",
                                project_id=project_id, nodes=dag_nodes
                            )
                            logger.info(f"Orchestrator: Constructed default DAG {dag_to_execute['dag_id']}")
                    else:
                        logger.info("Orchestrator: No affected tasks and no user prompt. Nothing for PlanAgent.")

                # Step 3: Trigger PlanAgent (PlanAgent still listens for DagDefinitionCreatedEvent)
                if dag_to_execute:
                    logger.info(f"Orchestrator: Ensuring DagDefinitionCreatedEvent is published for PlanAgent for DAG ID {dag_to_execute['dag_id']}")
                    # If BuildSurfAgent already published this event (and we received it above), 
                    # PlanAgent should pick it up. If we constructed a default DAG, we need to publish it.
                    if not user_prompt_for_pipeline and dag_to_execute: # We constructed it
                        plan_agent_trigger_event_id = str(uuid.uuid4())
                        dag_event_for_plan_agent = DagDefinitionCreatedEvent(
                            event_type="DagDefinitionCreatedEvent",
                            request_id=plan_agent_trigger_event_id,
                            project_id=dag_to_execute.get("project_id", project_id),
                            dag=dag_to_execute,
                            raw_llm_response= None,
                            timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                        )
                        # No need to use MessageRouter here if PlanAgent directly subscribes to this event type
                        # The channel should match PlanAgent's subscription
                        plan_agent_dag_channel = f"events.project.{dag_to_execute.get('project_id', project_id or 'global')}.dag.created"
                        self.event_bus.publish(plan_agent_dag_channel, dag_event_for_plan_agent) # Direct publish
                        logger.info(f"Orchestrator published default DagDefinitionCreatedEvent to {plan_agent_dag_channel}")


                    dag_execution_status_event_data = await self._wait_for_event(
                        expected_event_type="DagExecutionStatusEvent",
                        correlation_id=dag_to_execute["dag_id"],
                        correlation_id_field_in_event="dag_id",
                        event_channel_pattern=f"events.project.{dag_to_execute.get('project_id', project_id or 'global')}.dag.{dag_to_execute['dag_id']}.execution_status",
                        timeout_seconds=3600 
                    )
                    # ... (handle dag_execution_status_event_data as before) ...
                    if not (dag_execution_status_event_data and dag_execution_status_event_data.get("status") == "COMPLETED_SUCCESS"):
                        final_dag_status = dag_execution_status_event_data.get("status") if dag_execution_status_event_data else "TIMEOUT"
                        logger.error(f"Orchestrator: DAG execution for {commit_sha} did not succeed. Status: {final_dag_status}. Halting deployment flow.")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"DAG failed or timed out: {final_dag_status}"))
                        return
                elif affected_tasks: # No DAG to execute, but tasks were affected
                    logger.warning(f"Orchestrator: Affected tasks identified, but no DAG was executed for {commit_sha}. Flow incomplete.")
                    if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "No DAG executed"))
                    return
                # Else (no affected tasks, no user prompt) means flow might complete successfully here if that's desired.

                # Step 4: SecurityAgent scan (as before, wait for its event)
                logger.info(f"Orchestrator: Checking for SecurityAgent scan results for commit {commit_sha}")
                # ... (logic for _wait_for_event for SecurityScanResultEvent as before) ...
                # ... (decision logic based on security findings) ...

                # Step 5: CI_CD_Agent deploy using MessageRouter (if all previous steps are satisfactory)
                logger.info(f"Orchestrator: Dispatching 'RequestServiceDeployment' for project {project_id}")
                deploy_req_id = str(uuid.uuid4())
                service_to_deploy = project_id # Simplified assumption, needs refinement

                deployment_payload = {
                    "request_id": deploy_req_id,
                    "project_id": project_id,
                    "service_name": service_to_deploy, 
                    "commit_sha": commit_sha,
                    "target_environment": "staging", # Example
                    "triggered_by": f"OrchestratorFlow-{request_id}"
                }
                try:
                    await self.message_router.dispatch(logical_message_type="RequestServiceDeployment", payload=deployment_payload)
                except (MessageRouteNotFoundError, InvalidMessagePayloadError) as e_dispatch:
                    logger.error(f"Orchestrator: Failed to dispatch RequestServiceDeployment: {e_dispatch}")
                    if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"Dispatch failed: {e_dispatch}"))
                    return

                final_deployment_status_data = await self._wait_for_event(
                    expected_event_type="DeploymentStatusEvent",
                    correlation_id=deploy_req_id,
                    correlation_id_field_in_event="request_id",
                    event_channel_pattern=f"events.project.{project_id}.service.{service_to_deploy}.deployment.status",
                    timeout_seconds=1800 
                )
                # ... (handle final_deployment_status_data as before) ...
                if final_deployment_status_data and final_deployment_status_data.get("status") == "SUCCESSFUL":
                     logger.info(f"Orchestrator: Deployment of {service_to_deploy} for {project_id} commit {commit_sha} reported as SUCCESSFUL.")
                     if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                else:
                    status_msg = final_deployment_status_data.get("status", "TIMEOUT") if final_deployment_status_data else "TIMEOUT"
                    logger.error(f"Orchestrator: Deployment FAILED/timed out. Status: {status_msg}")
                    if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"Deployment failed: {status_msg}"))

                logger.info(f"Orchestrator: Full build flow for request '{request_id}' completed.")

            except Exception as e: # Catch-all for the main flow logic
                logger.error(f"Orchestrator: Unhandled error in full build flow '{request_id}': {e}", exc_info=True)
                if _tracer and _trace_api: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Orchestration flow error"))


    async def listen_for_orchestration_commands(self): # Placeholder listener
        logger.info(f"{SERVICE_NAME_ORCHESTRATOR} ready (placeholder listener).")
        while True: await asyncio.sleep(3600) 

# --- Example Usage Block (for testing/demonstration if run directly) ---
async def run_orchestration_example_flow():
    # ... (Example usage from response #57, but Orchestrator now takes MessageRouter) ...
    # ... This example needs careful review to ensure it correctly instantiates MessageRouter
    #     and passes it to Orchestrator, and that the payloads for message_router.dispatch align
    #     with what routing_rules.py expects for those logical message types.
    if not os.getenv("REDIS_URL"):
        print("CRITICAL: REDIS_URL environment variable must be set to run the Orchestrator example.", file=sys.stderr)
        return
    if not logging.getLogger().hasHandlers():
         logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                            format='%(asctime)s - EXAMPLE - %(name)s - %(levelname)s - %(message)s')

    logger.info("--- Orchestrator Example (with MessageRouter): Initializing Dependencies ---")
    try:
        event_bus_instance = EventBus()
        if not event_bus_instance.redis_client:
            logger.error("Example: EventBus connection failed. Aborting.")
            return

        agent_registry_instance = AgentRegistry() 
        shared_memory_instance = SharedMemoryStore() # Assumes EventBus for Redis is shared or re-init
        if REDIS_URL and not shared_memory_instance.redis_client: # Check if SharedMemoryStore also connected
            logger.warning("Example: SharedMemoryStore may not be connected to Redis.")

        message_router_instance = MessageRouter(event_bus_instance) # Initialize MessageRouter

        orchestrator = Orchestrator(agent_registry_instance, event_bus_instance, shared_memory_instance, message_router_instance)
    except Exception as e:
        logger.error(f"Failed to initialize core components for example: {e}", exc_info=True)
        return

    project_id_to_test = "project_zeta" 
    commit_sha_to_test = hashlib.sha1(os.urandom(16)).hexdigest()[:10]
    mock_changed_files_list: List[str] = [f"apps/{project_id_to_test}/src/component.py", "common/utils.py"]

    example_pipeline_prompt = "Standard CI: lint, test security implications, build, then deploy to staging if all good."

    logger.info(f"--- Starting Orchestrator Example Flow (using MessageRouter) for Project: {project_id_to_test} ---")
    try:
        await orchestrator.run_full_build_flow(
            project_id=project_id_to_test,
            commit_sha=commit_sha_to_test,
            changed_files_list=mock_changed_files_list, 
            user_prompt_for_pipeline=example_pipeline_prompt
        )
    except Exception as e:
        logger.error(f"Error during example orchestration flow: {e}", exc_info=True)
    finally:
        logger.info(f"--- Orchestrator Example Flow for Project: {project_id_to_test} Finished ---")


if __name__ == "__main__":
    import sys # For example stderr print
    if os.getenv("RUN_ORCHESTRATOR_EXAMPLE") == "true":
        logger.info("Attempting to run Orchestrator example flow with MessageRouter...")
        # ... (rest of the __main__ block from response #57 for running the example) ...
        if not REDIS_URL_SHARED_MEM: 
            print("CRITICAL: REDIS_URL env var must be set for Orchestrator example.", file=sys.stderr)
        else:
            try: asyncio.run(run_orchestration_example_flow())
            except KeyboardInterrupt: logger.info("Orchestrator example flow interrupted.")
            except Exception as e: logger.critical(f"Orchestrator example failed: {e}", exc_info=True)
    else:
        logger.info("Orchestrator module loaded. RUN_ORCHESTRATOR_EXAMPLE=true to run example.")
        # Placeholder for running as a service, if it listens for commands
        # async def start_default_listener():
        #     if not REDIS_URL_SHARED_MEM: return
        #     # ... init components ...
        #     # orchestrator = Orchestrator(...)
        #     # await orchestrator.listen_for_orchestration_commands() 
        # if REDIS_URL_SHARED_MEM: asyncio.run(start_default_listener())
