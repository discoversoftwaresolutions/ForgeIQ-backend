# ==============================================
# 📁 core/orchestrator/main_orchestrator.py
# ==============================================
import os
import json
import asyncio
import logging
import uuid
import datetime
import time  # <<< Added for time.monotonic()
import hashlib # <<< Added for example usage block
from typing import Dict, Any, Optional, List

# --- Observability Setup ---
SERVICE_NAME_ORCHESTRATOR = "Orchestrator"
LOG_LEVEL_ORCHESTRATOR = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL_ORCHESTRATOR,
    format=f'%(asctime)s - {SERVICE_NAME_ORCHESTRATOR} - %(name)s - %(levelname)s - %(message)s'
)
_tracer = None # Module-level tracer
_trace_api = None # Module-level trace_api for Status, StatusCode
try:
    from opentelemetry import trace as otel_trace_api
    _tracer = otel_trace_api.get_tracer(__name__) 
    _trace_api = otel_trace_api
except ImportError:
    logging.getLogger(__name__).info("OpenTelemetry API not found. Orchestrator will operate without distributed tracing.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

# Project-local imports (ensure PYTHONPATH is set correctly in execution environment)
from core.event_bus.redis_bus import EventBus
from core.agent_registry import AgentRegistry, AgentRegistrationInfo # AgentEndpoint, AgentCapability used via AgentRegistrationInfo
from core.shared_memory import SharedMemoryStore
from interfaces.types.events import (
    NewCommitEvent, FileChange,
    PipelineGenerationRequestEvent, PipelineGenerationUserPrompt,
    DagDefinitionCreatedEvent, DagDefinition, # Added DagDefinition
    DagExecutionStatusEvent, TaskStatusUpdateEvent,
    TestFailedEvent, PatchSuggestedEvent, SecurityScanResultEvent,
    DeploymentRequestEvent, DeploymentStatusEvent
)
# import httpx # Not directly used by Orchestrator class in this version, but could be if calling agents via HTTP

DEFAULT_EVENT_TIMEOUT = 300 # 5 minutes

class Orchestrator:
    def __init__(self, agent_registry: AgentRegistry, event_bus: EventBus, shared_memory: SharedMemoryStore):
        logger.info("Initializing Orchestrator...")
        self.agent_registry = agent_registry
        self.event_bus = event_bus
        self.shared_memory = shared_memory
        # self.http_client = httpx.AsyncClient(timeout=60.0) # If making API calls
        if not self.event_bus.redis_client: # Check critical dependencies
             logger.error("Orchestrator critical: EventBus not connected during initialization.")
        if not self.agent_registry: # Assuming AgentRegistry itself is lightweight to init
             logger.error("Orchestrator critical: AgentRegistry not provided during initialization.")
        if not self.shared_memory.redis_client: # Check if shared_memory relies on redis and it's up
             logger.warning("Orchestrator: SharedMemoryStore may not be connected to Redis.")

        logger.info("Orchestrator Initialized.")

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        """Helper to start a trace span only if tracing is properly initialized."""
        if _tracer and _trace_api:
            span = _tracer.start_span(f"orchestrator.{operation_name}", context=parent_context)
            for k, v in attrs.items():
                span.set_attribute(k, v)
            return span

        class NoOpSpan:
            def __enter__(self): return self
            def __exit__(self, exc_type, exc_val, exc_tb): pass
            def set_attribute(self, key, value): pass
            def record_exception(self, exception, attributes=None): pass # Added attributes
            def set_status(self, status): pass
            def end(self): pass
        return NoOpSpan()

    async def _publish_command_event(self, channel: str, event_data: Dict[str, Any]):
        # This method now seems specific enough not to need its own span,
        # as it would be called from within a traced operation.
        if self.event_bus.redis_client:
            self.event_bus.publish(channel, event_data) # EventBus.publish has its own logging
            logger.info(f"Orchestrator published command to '{channel}': {event_data.get('event_type')}")
        else:
            logger.error(f"Orchestrator: EventBus not connected. Cannot publish command to '{channel}'.")


    async def _wait_for_event(self,
                              expected_event_type: str,
                              correlation_id: str,
                              correlation_id_field_in_event: str,
                              event_channel_pattern: str,
                              timeout_seconds: int = DEFAULT_EVENT_TIMEOUT
                             ) -> Optional[Dict[str, Any]]:
        if not self.event_bus.redis_client:
            logger.error(f"Cannot wait for event '{expected_event_type}': EventBus not connected.")
            return None

        span_attrs = {
            "orchestrator.wait.event_type": expected_event_type,
            "orchestrator.wait.correlation_id": correlation_id,
            "orchestrator.wait.channel_pattern": event_channel_pattern,
            "orchestrator.wait.timeout": timeout_seconds
        }
        span = self._start_trace_span_if_available("wait_for_event", **span_attrs)

        pubsub = None # Initialize pubsub to None
        try:
            with span: #type: ignore
                pubsub = self.event_bus.subscribe_to_channel(event_channel_pattern) # Assuming this method is synchronous
                if not pubsub:
                    logger.error(f"Failed to subscribe to pattern '{event_channel_pattern}' for Orchestrator.")
                    if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Subscription failed"))
                    return None

                logger.info(f"Orchestrator waiting for event '{expected_event_type}' with {correlation_id_field_in_event}='{correlation_id}' on pattern '{event_channel_pattern}'")

                start_wait_time = time.monotonic()
                while True:
                    if time.monotonic() - start_wait_time > timeout_seconds:
                        logger.warning(f"Timeout waiting for event '{expected_event_type}' with {correlation_id_field_in_event}='{correlation_id}'.")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Event wait timeout"))
                        return None

                    # Run blocking pubsub.get_message in a separate thread
                    message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)

                    if message and message["type"] == "pmessage": # pmessage for pattern subscriptions
                        try:
                            event_data = json.loads(message["data"].decode('utf-8')) # Ensure decoding from bytes
                            if event_data.get("event_type") == expected_event_type and \
                               event_data.get(correlation_id_field_in_event) == correlation_id:
                                logger.info(f"Orchestrator received expected event '{expected_event_type}': {message_summary(event_data)}")
                                if _tracer and _trace_api: span.add_event("ReceivedExpectedEvent"); span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                                return event_data
                        except json.JSONDecodeError:
                            logger.error(f"Orchestrator: Could not decode JSON from event: {message['data'][:100]}")
                        except Exception as e: # Catch other errors during message processing
                            logger.error(f"Orchestrator: Error processing received message: {e}")

                    await asyncio.sleep(0.2) # Brief sleep to yield control, making the loop less tight
        except Exception as e: # Catch errors from subscribe_to_channel or other unexpected issues
            logger.error(f"Orchestrator: Exception in _wait_for_event: {e}", exc_info=True)
            if _tracer and _trace_api: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Wait for event failed"))
        finally:
            if pubsub:
                try:
                    # These are blocking calls, run in thread
                    if hasattr(pubsub, 'punsubscribe'): await asyncio.to_thread(pubsub.punsubscribe, event_channel_pattern)
                    if hasattr(pubsub, 'close'): await asyncio.to_thread(pubsub.close)
                except Exception as e_close:
                    logger.error(f"Error closing pubsub in _wait_for_event: {e_close}")
        return None


        async def run_full_build_flow(self, project_id: str, commit_sha: str, 
                                    changed_files_list: List[str], # Simplified: list of changed file paths
                                    user_prompt_for_pipeline: Optional[str] = None):
            """
            Example orchestrated flow.
            `changed_files_list` is a simple list of strings representing file paths.
            """
            request_id = str(uuid.uuid4())
            span = self._start_trace_span_if_available("run_full_build_flow", 
                                                       project_id=project_id, commit_sha=commit_sha, 
                                                       request_id=request_id, has_user_prompt=bool(user_prompt_for_pipeline))
            logger.info(f"Orchestrator: Starting full build flow for project '{project_id}', commit '{commit_sha}', request_id '{request_id}'")

            try:
                with span: #type: ignore
                    # Step 1: Trigger DependencyAgent by publishing NewCommitEvent
                    logger.info(f"Orchestrator: Publishing NewCommitEvent for project '{project_id}'.")
                    new_commit_event_id = str(uuid.uuid4())
                    # Construct FileChange objects from the list of strings
                    file_change_objects: List[FileChange] = [{"file_path": fp, "change_type": "modified"} for fp in changed_files_list]

                    new_commit_payload = NewCommitEvent(
                        event_type="NewCommitEvent", event_id=new_commit_event_id, project_id=project_id,
                        commit_sha=commit_sha, changed_files=file_change_objects, 
                        timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                    )
                    dep_agent_channel_target = f"events.project.{project_id}.new_commit"
                    await self._publish_command_event(dep_agent_channel_target, new_commit_payload)

                    affected_tasks_event_data = await self._wait_for_event(
                        expected_event_type="AffectedTasksIdentifiedEvent",
                        correlation_id=new_commit_event_id,
                        correlation_id_field_in_event="triggering_event_id",
                        event_channel_pattern=f"events.project.{project_id}.affected_tasks.identified"
                    )

                    if not affected_tasks_event_data:
                        logger.error(f"Orchestrator: Did not receive AffectedTasksIdentifiedEvent. Halting flow.")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "No AffectedTasksIdentifiedEvent"))
                        return

                    affected_tasks: List[str] = affected_tasks_event_data.get("affected_tasks", [])
                    logger.info(f"Orchestrator: Affected tasks from DependencyAgent: {affected_tasks}")
                    if _tracer: span.set_attribute("flow.affected_tasks_count", len(affected_tasks))
                    # If affected_tasks is empty, the DAG might be empty or just baseline tasks

                    # Step 2: (Optional) DAG generation via BuildSurfAgent
                    dag_to_execute: Optional[DagDefinition] = None
                    if user_prompt_for_pipeline:
                        logger.info(f"Orchestrator: Requesting DAG generation from BuildSurfAgent.")
                        pipeline_req_id = str(uuid.uuid4())
                        user_prompt_details = PipelineGenerationUserPrompt(
                            prompt_text=user_prompt_for_pipeline,
                            target_project_id=project_id
                        )
                        pipeline_req_event = PipelineGenerationRequestEvent(
                            event_type="PipelineGenerationRequestEvent",
                            request_id=pipeline_req_id,
                            user_prompt_data=user_prompt_details,
                            timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                        )
                        buildsurf_request_channel = "events.buildsurf.generate_dag.request" # As defined in BuildSurfAgent
                        await self._publish_command_event(buildsurf_request_channel, pipeline_req_event)

                        dag_created_event_data = await self._wait_for_event(
                            expected_event_type="DagDefinitionCreatedEvent",
                            correlation_id=pipeline_req_id,
                            correlation_id_field_in_event="request_id",
                            event_channel_pattern=f"events.project.{project_id or 'global'}.dag.created"
                        )
                        if dag_created_event_data and "dag" in dag_created_event_data:
                            dag_to_execute = dag_created_event_data["dag"] # This is DagDefinition
                            logger.info(f"Orchestrator: DAG {dag_to_execute['dag_id']} received from BuildSurfAgent.")
                            if _tracer: span.set_attribute("flow.dag_id_from_buildsurf", dag_to_execute['dag_id'])
                        else:
                            logger.error(f"Orchestrator: Failed to get DAG from BuildSurfAgent for request {pipeline_req_id}.")
                            # Decide: Halt or proceed with a default DAG? For now, halt if dynamic DAG was expected.
                            if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "BuildSurfAgent DAG generation failed"))
                            return
                    else:
                        # No user prompt, so we need a way to get a default/standard DAG for the project
                        # This could involve looking up a predefined DAG, or constructing one based on affected_tasks
                        logger.info(f"Orchestrator: No user prompt for pipeline. Constructing/fetching default DAG based on affected tasks: {affected_tasks}")
                        # Placeholder: Construct a simple DAG from affected_tasks.
                        # This part needs more robust logic based on how your system defines default pipelines.
                        if affected_tasks: # Only create a DAG if there are tasks
                            dag_nodes: List[DagNode] = []
                            for i, task_type_name in enumerate(affected_tasks):
                                dag_nodes.append(DagNode(
                                    id=f"{task_type_name}_{i}",
                                    task_type=task_type_name, # This must match what PlanAgent's task_runner expects
                                    dependencies=[] # Simplistic: no inter-dependencies between these tasks for now
                                ))
                            if dag_nodes:
                                dag_to_execute = DagDefinition(
                                    dag_id=f"default_dag_{project_id}_{commit_sha[:7]}_{str(uuid.uuid4())[:8]}",
                                    project_id=project_id,
                                    nodes=dag_nodes
                                )
                                logger.info(f"Orchestrator: Constructed default DAG {dag_to_execute['dag_id']} with tasks: {affected_tasks}")
                                if _tracer: span.set_attribute("flow.dag_id_constructed", dag_to_execute['dag_id'])
                            else:
                                logger.info("Orchestrator: No affected tasks, so no default DAG constructed.")
                        else:
                            logger.info("Orchestrator: No affected tasks and no user prompt. Nothing to execute with PlanAgent.")

                    # Step 3: Trigger PlanAgent to execute the DAG
                    if dag_to_execute:
                        logger.info(f"Orchestrator: Publishing DagDefinitionCreatedEvent for PlanAgent to execute DAG ID {dag_to_execute['dag_id']}")
                        # PlanAgent listens for DagDefinitionCreatedEvent.
                        # We need to ensure the event has all fields PlanAgent expects.
                        # The request_id here would be for PlanAgent's context.
                        plan_agent_trigger_event_id = str(uuid.uuid4()) # Or use the original request_id if appropriate
                        dag_event_for_plan_agent = DagDefinitionCreatedEvent(
                            event_type="DagDefinitionCreatedEvent",
                            request_id=plan_agent_trigger_event_id, # This event's own ID or the overall request_id
                            project_id=dag_to_execute.get("project_id", project_id),
                            dag=dag_to_execute,
                            raw_llm_response= None, # Not from LLM if default DAG
                            timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                        )
                        # Publish to the channel PlanAgent listens to
                        plan_agent_dag_channel = f"events.project.{dag_to_execute.get('project_id', project_id or 'global')}.dag.created"
                        await self._publish_command_event(plan_agent_dag_channel, dag_event_for_plan_agent)

                        dag_execution_status_event_data = await self._wait_for_event(
                            expected_event_type="DagExecutionStatusEvent",
                            correlation_id=dag_to_execute["dag_id"],
                            correlation_id_field_in_event="dag_id",
                            event_channel_pattern=f"events.project.{dag_to_execute.get('project_id', project_id or 'global')}.dag.{dag_to_execute['dag_id']}.execution_status",
                            timeout_seconds=3600 # Potentially long DAG execution
                        )
                        if dag_execution_status_event_data:
                            final_dag_status = dag_execution_status_event_data.get("status")
                            logger.info(f"Orchestrator: DAG {dag_to_execute['dag_id']} execution completed with status: {final_dag_status}")
                            if _tracer: span.set_attribute("flow.dag_final_status", final_dag_status)
                            if final_dag_status != "COMPLETED_SUCCESS":
                                logger.error(f"Orchestrator: DAG execution for {commit_sha} was not successful. Halting deployment flow.")
                                if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"DAG failed: {final_dag_status}"))
                                return 
                        else:
                            logger.error(f"Orchestrator: Did not receive final DAGExecutionStatusEvent for {dag_to_execute['dag_id']}. Halting.")
                            if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "No DAG completion event"))
                            return
                    elif affected_tasks: # DAG wasn't created but there were affected tasks - indicates an issue
                        logger.warning(f"Orchestrator: Affected tasks {affected_tasks} identified, but no DAG was executed. Flow incomplete.")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "No DAG executed despite affected tasks"))
                        return


                    # Step 4: SecurityAgent scan (listens to NewCommitEvent, so might have already run)
                    # We can wait for its result for this commit to make a deployment decision.
                    logger.info(f"Orchestrator: Checking for SecurityAgent scan results for commit {commit_sha}")
                    security_scan_result_data = await self._wait_for_event(
                        expected_event_type="SecurityScanResultEvent",
                        correlation_id=new_commit_event_id, # Correlate with the NewCommitEvent that triggered scans
                        correlation_id_field_in_event="triggering_event_id",
                        event_channel_pattern=f"events.project.{project_id}.security.scan_result",
                        timeout_seconds=600 
                    )
                    if security_scan_result_data:
                        findings_count = len(security_scan_result_data.get("findings", []))
                        logger.info(f"Orchestrator: Security scan completed. Findings: {findings_count}")
                        if _tracer: span.set_attribute("flow.security_findings_count", findings_count)
                        # TODO: Add logic here to evaluate findings and decide if deployment should proceed.
                        # For V0.1, we'll log and proceed.
                        if findings_count > 0:
                            logger.warning(f"Orchestrator: Security scan found {findings_count} issues for {commit_sha}. Proceeding with deployment for V0.1.")
                    else:
                        logger.warning(f"Orchestrator: No SecurityScanResultEvent received for commit {commit_sha}. Proceeding with deployment with caution.")
                        if _tracer: span.set_attribute("flow.security_scan_status", "not_received")

                    # Step 5: CI_CD_Agent deploy (if all previous steps are satisfactory)
                    logger.info(f"Orchestrator: Requesting deployment for project {project_id}, commit {commit_sha}")
                    deploy_req_id = str(uuid.uuid4())
                    # This needs to know which specific *service* within the project to deploy.
                    # This logic needs to be more sophisticated. For now, assume project_id is the service name.
                    service_to_deploy = project_id 

                    deployment_request_payload = DeploymentRequestEvent(
                        event_type="DeploymentRequestEvent",
                        request_id=deploy_req_id,
                        project_id=project_id,
                        service_name=service_to_deploy, 
                        commit_sha=commit_sha,
                        target_environment="staging", # Example, could be dynamic
                        triggered_by=f"OrchestratorFlow-{request_id}",
                        timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                    )
                    # Publish to the channel CI_CD_Agent listens to
                    cicd_request_channel = f"events.project.{project_id}.deployment.request" 
                    await self._publish_command_event(cicd_request_channel, deployment_request_payload)

                    final_deployment_status_data = await self._wait_for_event(
                        expected_event_type="DeploymentStatusEvent",
                        correlation_id=deploy_req_id,
                        correlation_id_field_in_event="request_id",
                        event_channel_pattern=f"events.project.{project_id}.service.{service_to_deploy}.deployment.status",
                        timeout_seconds=1800 
                    )

                    if final_deployment_status_data and final_deployment_status_data.get("status") == "SUCCESSFUL":
                        logger.info(f"Orchestrator: Deployment of {service_to_deploy} for {project_id} commit {commit_sha} reported as SUCCESSFUL.")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                    else:
                        status_msg = final_deployment_status_data.get("status", "UNKNOWN") if final_deployment_status_data else "TIMEOUT"
                        logger.error(f"Orchestrator: Deployment of {service_to_deploy} for {project_id} commit {commit_sha} FAILED or timed out. Status: {status_msg}")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"Deployment failed: {status_msg}"))

                    logger.info(f"Orchestrator: Full build flow for request '{request_id}' completed.")

            except Exception as e:
                logger.error(f"Orchestrator: Unhandled error in full build flow '{request_id}': {e}", exc_info=True)
                if _tracer and _trace_api: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"Orchestration flow error: {str(e)}"))


    async def listen_for_orchestration_commands(self):
        """ Placeholder: In a real system, this might listen for high-level orchestration requests. """
        logger.info(f"{SERVICE_NAME_ORCHESTRATOR} is ready to orchestrate (currently has no active command listener).")
        logger.info("To trigger an example flow (if enabled), ensure RUN_ORCHESTRATOR_EXAMPLE is set.")
        # Example:
        # command_channel = "commands.orchestrator.run_build_flow"
        # pubsub = self.event_bus.subscribe_to_channel(command_channel)
        # if pubsub:
        #     logger.info(f"Orchestrator listening on {command_channel}")
        #     for message in pubsub.listen(): # This is blocking, use async pattern
        #         if message["type"] == "message":
        #             data = json.loads(message["data"])
        #             if data.get("command") == "run_full_build_flow":
        #                 asyncio.create_task(self.run_full_build_flow(
        #                     project_id=data["project_id"],
        #                     commit_sha=data["commit_sha"],
        #                     changed_files_list=data["changed_files_list"],
        #                     user_prompt_for_pipeline=data.get("user_prompt_for_pipeline")
        #                 ))
        while True: # Keep alive if run as a service for other potential triggers
            await asyncio.sleep(3600) 


# --- Example Usage Block (for testing/demonstration if run directly) ---
async def run_orchestration_example_flow():
    if not os.getenv("REDIS_URL"):
        print("CRITICAL: REDIS_URL environment variable must be set to run the Orchestrator example.", file=sys.stderr)
        return

    # Ensure basic logging is configured for the example itself if not already by OTel setup
    if not logging.getLogger().hasHandlers(): # Check if root logger has handlers
         logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                            format='%(asctime)s - EXAMPLE - %(name)s - %(levelname)s - %(message)s')

    logger.info("--- Orchestrator Example: Initializing Dependencies ---")
    # In a real application, these dependencies would be managed and injected,
    # possibly as singletons or via a dependency injection framework.
    try:
        agent_registry = AgentRegistry() 
        event_bus = EventBus()
        shared_memory = SharedMemoryStore()
    except Exception as e:
        logger.error(f"Failed to initialize core components for example: {e}", exc_info=True)
        return

    if not event_bus.redis_client:
        logger.error("Orchestrator Example: Failed to connect to EventBus. Aborting example.")
        return

    orchestrator = Orchestrator(agent_registry, event_bus, shared_memory)

    # Example project and commit details
    project_id_to_test = "project_gamma" # Example project
    commit_sha_to_test = hashlib.sha1(os.urandom(16)).hexdigest()[:10]
    mock_changed_files_list: List[str] = [
        f"apps/{project_id_to_test}/src/main.py", # Assuming a structure
        "requirements.txt"
    ]
    # Convert to FileChange objects
    mock_file_changes: List[FileChange] = [{"file_path": fp, "change_type": "modified"} for fp in mock_changed_files_list]

    example_pipeline_prompt = "A standard CI/CD pipeline: lint all code, run unit tests, build the main application binary, then deploy to the staging environment. If staging deployment is successful, seek manual approval then deploy to production."

    logger.info(f"--- Starting Orchestrator Example Flow for Project: {project_id_to_test}, Commit: {commit_sha_to_test} ---")
    try:
        # This call will publish events that (if other agents are running and subscribed)
        # should lead to a sequence of operations. This example relies on those other agents
        # responding on the event bus for the _wait_for_event calls to succeed.
        await orchestrator.run_full_build_flow(
            project_id=project_id_to_test,
            commit_sha=commit_sha_to_test,
            changed_files_list=mock_file_changes, # Pass the simplified list
            user_prompt_for_pipeline=example_pipeline_prompt
        )
    except Exception as e:
        logger.error(f"Error during example orchestration flow: {e}", exc_info=True)
    finally:
        logger.info(f"--- Orchestrator Example Flow for Project: {project_id_to_test} Finished ---")
        # Clean up resources if http_client was used and needs closing
        # if hasattr(orchestrator, 'http_client') and orchestrator.http_client:
        #    await orchestrator.http_client.aclose()


if __name__ == "__main__":
    # This example is complex and assumes other agents are running and responsive on Redis.
    # To run it:
    # 1. Ensure Redis is running and REDIS_URL is set.
    # 2. Ensure all dependent core modules (EventBus, AgentRegistry, SharedMemoryStore, Observability) are correct.
    # 3. Ensure all agents this orchestrator tries to trigger (DependencyAgent, BuildSurfAgent, PlanAgent, SecurityAgent, CI_CD_Agent)
    #    are also running and subscribed to their respective command/event channels.
    # 4. Set the environment variable RUN_ORCHESTRATOR_EXAMPLE=true to execute the example flow.
    #
    # Otherwise, if not running the example, this script doesn't do much by default when run directly.
    # A real service would likely call `orchestrator.listen_for_orchestration_commands()` or similar.

    import sys # For sys.stderr in example runner check

    if os.getenv("RUN_ORCHESTRATOR_EXAMPLE") == "true":
        logger.info("Attempting to run Orchestrator example flow...")
        if not REDIS_URL_SHARED_MEM: # Check if REDIS_URL was loaded
            print("CRITICAL: REDIS_URL environment variable must be set to run the Orchestrator example.", file=sys.stderr)
        else:
            try:
                asyncio.run(run_orchestration_example_flow())
            except KeyboardInterrupt:
                logger.info("Orchestrator example flow interrupted by user.")
            except Exception as e:
                logger.critical(f"Orchestrator example flow failed catastrophically: {e}", exc_info=True)
    else:
        logger.info(
            "Orchestrator module loaded. Not running example flow (set RUN_ORCHESTRATOR_EXAMPLE=true to run it).\n"
            "If run as a service, it would typically start its main event loop or API server here."
        )
        # Example: Start a placeholder listener if not running the example.
        # async def start_default_listener():
        #     if not REDIS_URL_SHARED_MEM: return
        #     agent_registry = AgentRegistry()
        #     event_bus = EventBus()
        #     shared_memory = SharedMemoryStore()
        #     orchestrator = Orchestrator(agent_registry, event_bus, shared_memory)
        #     await orchestrator.listen_for_orchestration_commands() # This is a placeholder loop
        #
        # if REDIS_URL_SHARED_MEM:
        #    asyncio.run(start_default_listener())
