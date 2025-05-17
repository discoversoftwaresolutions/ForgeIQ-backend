# =======================================================
# 📁 agents/DependencyAgent/app/agent.py (V0.2 - CABGP)
# =======================================================
import os
import json
import datetime
import uuid
import asyncio
import logging
from typing import Dict, Any, Optional, List

# --- Observability Setup (as before) ---
SERVICE_NAME = "DependencyAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s')
_tracer = None; _trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing
    _tracer = setup_tracing(SERVICE_NAME)
    _trace_api = otel_trace_api
except ImportError: logging.getLogger(SERVICE_NAME).warning("DependencyAgent: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from core.build_graph import detect_changed_tasks as core_detect_changed_tasks, get_project_dag as core_get_project_dag
from interfaces.types.events import NewCommitEvent, AffectedTasksIdentifiedEvent, FileChange

# SDK for calling ForgeIQ-backend to access proprietary algorithms
try:
    from sdk.client import ForgeIQClient
    from sdk.exceptions import ForgeIQSDKError
    # from sdk.models import SDKAlgorithmContext # If needed for precise typing for SDK call
except ImportError:
    logger.error("DependencyAgent: ForgeIQ SDK not found. Proprietary algorithm integration will fail.")
    ForgeIQClient = None # type: ignore
    ForgeIQSDKError = Exception # type: ignore


# Configuration for using proprietary CABGP
USE_PROPRIETARY_CABGP = os.getenv("USE_PROPRIETARY_CABGP", "false").lower() == "true"
CABGP_ALGORITHM_ID = "CABGP" # The ID registered for this algorithm

COMMIT_EVENT_CHANNEL_PATTERN = "events.project.*.new_commit"
AFFECTED_TASKS_EVENT_CHANNEL_TEMPLATE = "events.project.{project_id}.affected_tasks.identified"

class DependencyAgent:
    def __init__(self):
        logger.info(f"Initializing DependencyAgent (V0.2 - CABGP Integration: {USE_PROPRIETARY_CABGP})...")
        self.event_bus = EventBus()
        self.sdk_client: Optional[ForgeIQClient] = None

        if USE_PROPRIETARY_CABGP:
            if ForgeIQClient:
                api_base_url = os.getenv("FORGEIQ_API_BASE_URL")
                api_key = os.getenv("FORGEIQ_API_KEY")
                if api_base_url:
                    try:
                        self.sdk_client = ForgeIQClient(base_url=api_base_url, api_key=api_key)
                        logger.info("DependencyAgent: ForgeIQ SDK client initialized for CABGP.")
                    except Exception as e_sdk:
                        logger.error(f"DependencyAgent: Failed to init ForgeIQ SDK client: {e_sdk}", exc_info=True)
                        self.sdk_client = None # Ensure it's None on failure
                else:
                    logger.error("DependencyAgent: USE_PROPRIETARY_CABGP is true, but FORGEIQ_API_BASE_URL is not set. CABGP calls will fail.")
            else:
                logger.error("DependencyAgent: USE_PROPRIETARY_CABGP is true, but ForgeIQClient SDK could not be imported.")

        if not self.event_bus.redis_client:
            logger.error("DependencyAgent critical: EventBus not connected.")
        logger.info("DependencyAgent V0.2 Initialized.")

    @property
    def tracer_instance(self): return _tracer
    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same helper as before) ...
        if self.tracer_instance and _trace_api:
            span = self.tracer_instance.start_span(f"dependency_agent.{operation_name}", context=parent_context)
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

    async def _determine_affected_tasks_with_cabgp(self, project_id: str, changed_files_paths: List[str], commit_sha: str) -> List[str]:
        """Uses the proprietary CABGP algorithm via SDK and ForgeIQ-backend."""
        if not self.sdk_client:
            logger.error(f"CABGP integration: SDK client not available for project {project_id}. Falling back to core logic.")
            # Fallback to core logic if SDK is not there
            return self._determine_affected_tasks_core(project_id, changed_files_paths)

        logger.info(f"CABGP integration: Requesting CABGP analysis for project '{project_id}', commit '{commit_sha}'.")

        # Prepare context for CABGP. This needs to match what your CABGP algorithm expects.
        # Your CABGP runAlgorithm(context) from response #92 expected: { dag, changeMap, fileSemantics }
        # We need to provide these or a compatible structure.
        # For V0.2, let's pass changed_files and project_id. The private AlgorithmAgent/CABGP
        # would be responsible for fetching the current DAG or fileSemantics if it needs them.
        context_data_for_cabgp = {
            "project_id": project_id,
            "commit_sha": commit_sha,
            "changed_files": changed_files_paths, # CABGP might want more than just paths, e.g. diffs
            "current_dag_hint": core_get_project_dag(project_id) # Provide current simple DAG as hint
        }

        span = self._start_trace_span_if_available("call_cabgp_via_sdk", project_id=project_id, algorithm_id=CABGP_ALGORITHM_ID)
        try:
            with span: #type: ignore
                # This calls ForgeIQClient.apply_proprietary_algorithm
                # which calls ForgeIQ-backend /api/forgeiq/algorithms/apply
                # which then calls your private stack's /invoke_proprietary_algorithm endpoint
                # that dynamically loads and runs the CABGP Python module.
                response = await self.sdk_client.apply_proprietary_algorithm(
                    algorithm_id=CABGP_ALGORITHM_ID,
                    context_data=context_data_for_cabgp,
                    project_id=project_id 
                )
                if _tracer and span and response: 
                    span.set_attribute("cabgp.response_status", response.get("status"))

                if response and response.get("status") == "success" and response.get("result"):
                    # Assume CABGP result contains a list of affected task IDs or names in a specific field
                    # e.g., response["result"]["affected_tasks_list"]
                    # This depends on the output structure of your CABGP.py run_algorithm
                    cabgp_result = response["result"]
                    affected_tasks = cabgp_result.get("impacted_nodes") or cabgp_result.get("affected_components") or cabgp_result.get("nodes_to_rebuild")

                    if affected_tasks is not None and isinstance(affected_tasks, list):
                        logger.info(f"CABGP successfully determined affected tasks for project {project_id}: {affected_tasks}")
                        if _tracer and _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                        return affected_tasks
                    else:
                        logger.warning(f"CABGP response for {project_id} was successful but affected tasks list is missing or not a list. Response: {message_summary(str(cabgp_result))}")
                        if _tracer and _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "CABGP Invalid Result Format"))
                else:
                    error_msg = response.get("error_message", "Unknown error from CABGP via API.") if response else "No response from CABGP via API."
                    logger.error(f"CABGP analysis failed or returned unexpected response for project {project_id}: {error_msg}")
                    if _tracer and _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"CABGP API call failed: {error_msg}"))
        except ForgeIQSDKError as e_sdk: # Catch specific SDK errors
            logger.error(f"SDK Error calling CABGP for project {project_id}: {e_sdk}", exc_info=True)
            if _tracer and _trace_api and span: span.record_exception(e_sdk); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "SDK Error for CABGP"))
        except Exception as e:
            logger.error(f"Unexpected error during CABGP call for project {project_id}: {e}", exc_info=True)
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "CABGP call unexpected error"))

        logger.warning(f"Falling back to core dependency logic for project {project_id} after CABGP issue.")
        return self._determine_affected_tasks_core(project_id, changed_files_paths)


    def _determine_affected_tasks_core(self, project_id: str, changed_files_paths: List[str]) -> List[str]:
        """Uses the core.build_graph logic (user-provided Python code)."""
        # (This is the logic from DependencyAgent V0.1 - response #73)
        span_attrs = {"project_id":project_id, "num_changed_files":len(changed_files_paths), "method":"core_logic"}
        span = self._start_trace_span_if_available("_determine_affected_tasks_core", **span_attrs)
        try:
            with span: #type: ignore
                if not changed_files_paths:
                    logger.info(f"Core: No changed files for project {project_id}, defaulting to full DAG.")
                    affected = core_get_project_dag(project_id) 
                    logger.info(f"Core: Defaulting to all tasks for {project_id}: {affected}")
                    if _tracer and span: span.set_attribute("core_logic.result_count", len(affected))
                    return affected

                logger.info(f"Core: Detecting affected tasks for {project_id} from {len(changed_files_paths)} changed file(s).")
                affected_tasks = core_detect_changed_tasks(project=project_id, changed_files=changed_files_paths)
                logger.info(f"Core: Affected tasks for {project_id}: {affected_tasks}")
                if _tracer and span: span.set_attribute("core_logic.result_count", len(affected_tasks))
                return affected_tasks
        except Exception as e:
            logger.error(f"Error in _determine_affected_tasks_core for {project_id}: {e}", exc_info=True)
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR))
            return core_get_project_dag(project_id) # Fallback to all tasks on error


    async def _handle_new_commit_event_logic(self, event: NewCommitEvent): # Updated to use CABGP or core
        project_id = event["project_id"]
        commit_sha = event["commit_sha"]
        changed_file_paths = [change['file_path'] for change in event.get("changed_files", [])]

        affected_tasks: List[str] = []
        if USE_PROPRIETARY_CABGP:
            logger.info(f"Attempting to use CABGP for dependency analysis for project {project_id}.")
            affected_tasks = await self._determine_affected_tasks_with_cabgp(project_id, changed_file_paths, commit_sha)
        else:
            logger.info(f"Using core build-graph logic for dependency analysis for project {project_id}.")
            affected_tasks = self._determine_affected_tasks_core(project_id, changed_file_paths)

        if affected_tasks is not None: # Check for None in case CABGP call had issues and didn't fallback properly
            if self.event_bus.redis_client:
                affected_event = AffectedTasksIdentifiedEvent(
                    event_type="AffectedTasksIdentifiedEvent",
                    triggering_event_id=event.get("event_id", str(uuid.uuid4())),
                    project_id=project_id, commit_sha=commit_sha, affected_tasks=affected_tasks,
                    timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                )
                channel = AFFECTED_TASKS_EVENT_CHANNEL_TEMPLATE.format(project_id=project_id)
                self.event_bus.publish(channel, affected_event) #type: ignore
                logger.info(f"Published AffectedTasksIdentifiedEvent for project {project_id}, commit {commit_sha}. Tasks: {affected_tasks}")
                if self.tracer_instance: self.tracer_instance.get_current_span().add_event("Published AffectedTasksIdentifiedEvent", {"num_affected_tasks": len(affected_tasks)})
            else:
                logger.error("Cannot publish AffectedTasksIdentifiedEvent: EventBus not connected.")
        else: # affected_tasks is None, indicating a failure in determination even after fallback
            logger.error(f"Failed to determine affected tasks for project {project_id}, commit {commit_sha}. No event published.")

    # ... (handle_new_commit_event wrapper, main_event_loop, main_async_runner, if __name__ == "__main__"
    #      remain structurally the same as V0.1 from response #73.
    #      Ensure they call the updated _handle_new_commit_event_logic correctly and handle async.)
    # For brevity, I'll paste the full structure for these main control flows.

    async def handle_new_commit_event(self, event_data_str: str): # Wrapper from V0.1
        span = self._start_trace_span_if_available("handle_new_commit_event")
        try:
            with span: #type: ignore
                event: NewCommitEvent = json.loads(event_data_str) #type: ignore
                if not (event.get("event_type") == "NewCommitEvent" and 
                        all(k in event for k in ["project_id", "commit_sha", "changed_files"])):
                    logger.error(f"Malformed NewCommitEvent: {event_data_str[:200]}")
                    if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Malformed event")); 
                    return

                if _tracer and span: span.set_attributes({
                    "messaging.system": "redis", "dependency_agent.event_id": event.get("event_id"),
                    "dependency_agent.project_id": event["project_id"], "dependency_agent.commit_sha": event["commit_sha"],
                    "dependency_agent.num_changed_files": len(event["changed_files"])
                })
                logger.info(f"DependencyAgent handling NewCommitEvent for project {event['project_id']}")
                await self._handle_new_commit_event_logic(event) # Call the main logic
        except json.JSONDecodeError: logger.error(f"Could not decode JSON: {event_data_str[:200]}"); 
        if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "JSON decode error"))
        except Exception as e: logger.error(f"Error handling NewCommitEvent: {e}", exc_info=True); 
        if _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Event handling failed"))

    async def main_event_loop(self): # From V0.1
        if not self.event_bus.redis_client: logger.critical("DependencyAgent: EventBus not connected. Exiting."); await asyncio.sleep(60); return
        pubsub = self.event_bus.subscribe_to_channel(COMMIT_EVENT_CHANNEL_PATTERN) #type: ignore
        if not pubsub: logger.critical(f"DependencyAgent: Failed to subscribe to {COMMIT_EVENT_CHANNEL_PATTERN}. Exiting."); await asyncio.sleep(60); return
        logger.info(f"DependencyAgent worker (V0.2) subscribed to '{COMMIT_EVENT_CHANNEL_PATTERN}', listening...")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0) #type: ignore
                if message and message["type"] == "pmessage":
                    await self.handle_new_commit_event(message["data"]) #type: ignore
                await asyncio.sleep(0.01)
        except KeyboardInterrupt: logger.info("DependencyAgent event loop interrupted.")
        except Exception as e: logger.error(f"Critical error in DependencyAgent event loop: {e}", exc_info=True)
        finally:
            logger.info("DependencyAgent shutting down pubsub...");
            if pubsub:
                try: await asyncio.to_thread(pubsub.punsubscribe, COMMIT_EVENT_CHANNEL_PATTERN); await asyncio.to_thread(pubsub.close) #type: ignore
                except: pass
            if self.sdk_client: await self.sdk_client.close() # Close SDK client if it was initialized
            logger.info("DependencyAgent shutdown complete.")

async def main_async_runner(): # From V0.1
    # Ensure sdk_client_factory can be imported if SDK is used directly by agent for other purposes
    # Or just pass None if only this CABGP call needs it.
    # For this version, __init__ creates the client if USE_PROPRIETARY_CABGP is true.
    agent = DependencyAgent()
    await agent.main_event_loop()

if __name__ == "__main__": # From V0.1
    try:
        asyncio.run(main_async_runner())
    except KeyboardInterrupt:
        logger.info("DependencyAgent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"DependencyAgent failed to start or unhandled error: {e}", exc_info=True)
