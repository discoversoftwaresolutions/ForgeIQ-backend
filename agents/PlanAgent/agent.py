# ============================================
# 📁 agents/PlanAgent/agent.py (V0.2 Enhanced)
# ============================================
import os
import json
import datetime
import uuid
import asyncio
import logging
from collections import defaultdict
from typing import Dict, Any, Optional, List, Set

# --- Observability Setup (as before) ---
SERVICE_NAME = "PlanAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s')
_tracer = None; _trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing
    _tracer = setup_tracing(SERVICE_NAME)
    _trace_api = otel_trace_api
except ImportError: logging.getLogger(SERVICE_NAME).warning("PlanAgent: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from core.task_runner import run_task 
from core.shared_memory import SharedMemoryStore # <<< Ensure this is imported
from interfaces.types.events import (
    DagDefinition, DagNode, DagDefinitionCreatedEvent,
    TaskStatus, TaskStatusUpdateEvent, DagExecutionStatusEvent
)

DAG_DEFINITION_EVENT_CHANNEL_PATTERN = "events.project.*.dag.created"
TASK_STATUS_EVENT_CHANNEL_TEMPLATE = "events.project.{project_id}.dag.{dag_id}.task_status"
DAG_EXECUTION_STATUS_CHANNEL_TEMPLATE = "events.project.{project_id}.dag.{dag_id}.execution_status"

# Key for SharedMemoryStore where ForgeIQ-backend will query DAG status
DAG_STATUS_SUMMARY_KEY_TEMPLATE = "dag_execution:{dag_id}:status_summary" 
# Expiry for this key in SharedMemoryStore (e.g., 7 days)
DAG_STATUS_EXPIRY_SECONDS = int(os.getenv("PLAN_AGENT_DAG_STATUS_EXPIRY_SECONDS", 7 * 24 * 60 * 60))


class PlanAgent:
    def __init__(self):
        logger.info("Initializing PlanAgent (V0.2 with SharedMemory status persistence)...")
        self.event_bus = EventBus()
        self.shared_memory = SharedMemoryStore() # <<< INITIALIZE SharedMemoryStore
        self.active_dags: Dict[str, Dict[str, Any]] = {}

        if not self.event_bus.redis_client:
            logger.error("PlanAgent: EventBus not connected.")
        if not self.shared_memory.redis_client: # Check SharedMemory's Redis client
            logger.warning("PlanAgent: SharedMemoryStore not connected to Redis. Will not persist final DAG status for API retrieval.")
        logger.info("PlanAgent V0.2 Initialized.")

    @property
    def tracer_instance(self): # Renamed from tracer to avoid conflict with module-level tracer
        return _tracer

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same helper as before from response #77 Orchestrator) ...
        if self.tracer_instance and _trace_api:
            span = self.tracer_instance.start_span(f"plan_agent.{operation_name}", context=parent_context)
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

    async def _store_final_dag_status_in_shared_memory(self, dag_id: str, status_event_data: DagExecutionStatusEvent):
        """Stores the final DAG execution status summary in SharedMemoryStore."""
        if not self.shared_memory.redis_client:
            logger.warning(f"DAG {dag_id}: Cannot store final status to SharedMemory, client not available.")
            return

        status_key = DAG_STATUS_SUMMARY_KEY_TEMPLATE.format(dag_id=dag_id)
        # The status_event_data is already a TypedDict. SharedMemoryStore's set_value will JSON serialize it.
        # This data should match what SDKDagExecutionStatusModel expects.
        # Crucially, it needs to include the "dag" definition (nodes) itself if the API is to return it.
        # The DagExecutionStatusEvent currently has "task_statuses", but not the original DAG node structure.
        # Let's assume for now, the event only has task_statuses, and the API needs to be smart.
        # OR, we enhance DagExecutionStatusEvent to include the original DagDefinition.
        # For now, let's store what the event has.

        # Retrieve the original DAG definition if it's stored in self.active_dags
        original_dag_definition = self.active_dags.get(dag_id, {}).get("dag")

        # Construct data for storage, potentially including the DAG definition itself.
        # This should align with SDKDagExecutionStatusModel for easy use by backend API.
        data_to_store = {
            "dag_id": status_event_data["dag_id"],
            "project_id": status_event_data.get("project_id"),
            "status": status_event_data["status"],
            "message": status_event_data.get("message"),
            "started_at": status_event_data["started_at"],
            "completed_at": status_event_data.get("completed_at"),
            "task_statuses": status_event_data["task_statuses"],
            "dag_definition_nodes": original_dag_definition.get("nodes") if original_dag_definition else None # For UI visualization
        }

        try:
            success = await self.shared_memory.set_value(
                status_key, 
                data_to_store, 
                expiry_seconds=DAG_STATUS_EXPIRY_SECONDS
            )
            if success:
                logger.info(f"DAG {dag_id}: Final status summary (Status: {status_event_data['status']}) stored to SharedMemory: '{status_key}'.")
            else:
                logger.error(f"DAG {dag_id}: Failed to store final status summary to SharedMemory for key '{status_key}'.")
        except Exception as e:
            logger.error(f"DAG {dag_id}: Exception storing final status to SharedMemory: {e}", exc_info=True)


    def _publish_dag_status(self, project_id: Optional[str], dag_id: str, status: str, 
                            all_task_statuses: List[TaskStatus], message: Optional[str]=None):
        # (Construct event_data as in response #61 / #77 for PlanAgent)
        dag_info = self.active_dags.get(dag_id, {}) # Get current DAG info
        start_time = dag_info.get("started_at", datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z")
        completion_time = None
        is_terminal_status = status.upper() in ["COMPLETED_SUCCESS", "FAILED", "COMPLETED_PARTIAL"]
        if is_terminal_status:
            completion_time = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"

        event_data = DagExecutionStatusEvent(
            event_type="DagExecutionStatusEvent", project_id=project_id, dag_id=dag_id, 
            status=status, message=message, started_at=start_time, completed_at=completion_time,
            task_statuses=all_task_statuses,
            timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        )

        if self.event_bus.redis_client:
            channel = DAG_EXECUTION_STATUS_CHANNEL_TEMPLATE.format(project_id=project_id or "global", dag_id=dag_id)
            self.event_bus.publish(channel, event_data) #type: ignore
            logger.info(f"Published DagExecutionStatusEvent to {channel}: DAG {dag_id} status {status}")

        # If it's a terminal status, also store it in SharedMemory
        if is_terminal_status:
            logger.info(f"DAG {dag_id} reached terminal state '{status}'. Scheduling storage of final status.")
            # Use asyncio.create_task to not block the current flow if _store_final_dag_status is async
            asyncio.create_task(self._store_final_dag_status_in_shared_memory(dag_id, event_data))


    # ... execute_dag_task, _process_dag_execution_cycle, execute_dag_orchestrator, 
    #     handle_dag_definition_event, main_event_loop ...
    # These methods remain largely the same as the robust V0.1 from response #61.
    # The key is that _publish_dag_status now also triggers _store_final_dag_status_in_shared_memory.
    # I'll re-paste the full agent logic for completeness, ensuring the new store method is called.
    # (The full PlanAgent from response #61 was very detailed, I'll integrate into that structure.)

    async def _publish_task_status(self, project_id: Optional[str], dag_id: str, task_status_data: TaskStatus): # Corrected definition from #61
        if self.event_bus.redis_client:
            event_data = TaskStatusUpdateEvent(
                event_type="TaskStatusUpdateEvent", project_id=project_id, dag_id=dag_id,
                task=task_status_data,
                timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            )
            channel = TASK_STATUS_EVENT_CHANNEL_TEMPLATE.format(project_id=project_id or "global", dag_id=dag_id)
            self.event_bus.publish(channel, event_data) #type: ignore
            logger.debug(f"Published TaskStatusUpdateEvent to {channel}: {task_status_data}")

    async def execute_dag_task(self, project_id: Optional[str], dag_id: str, task_id: str):
        # ... (as in response #61) ...
        dag_state = self.active_dags.get(dag_id)
        if not dag_state: logger.error(f"DAG {dag_id} not found. Cannot execute task {task_id}."); return
        node_map: Dict[str, DagNode] = dag_state["node_map"]
        task_def = node_map.get(task_id)
        if not task_def:
            logger.error(f"Task definition for {task_id} not found in DAG {dag_id}.")
            dag_state["task_statuses"][task_id] = TaskStatus(task_id=task_id, status='FAILED', message="Task definition not found") #type: ignore
            self._publish_task_status(project_id, dag_id, dag_state["task_statuses"][task_id])
            return

        logger.info(f"DAG {dag_id}: Executing task '{task_id}' (Type: {task_def['task_type']}) for project '{project_id}'.")
        current_task_status = TaskStatus(
            task_id=task_id, status='RUNNING', 
            started_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        )
        dag_state["task_statuses"][task_id] = current_task_status
        self._publish_task_status(project_id, dag_id, current_task_status)
        task_name_for_runner = task_def['task_type']
        if task_def.get('command'):
            logger.warning(f"DAG {dag_id}: Task '{task_id}' command {task_def['command']} present. Ensure task_runner handles this or maps task_type '{task_name_for_runner}'.")

        task_execution_project_context = project_id or dag_state["dag"].get("project_id", "default_project")
        try:
            loop = asyncio.get_running_loop()
            task_result_dict = await loop.run_in_executor(None, run_task, task_name_for_runner, task_execution_project_context)
            final_status_str = task_result_dict.get("status", "error").upper()
            if final_status_str == "ERROR": final_status_str = "FAILED" 

            current_task_status_update: Dict[str, Any] = { # Use dict for update method
                "status":final_status_str, "message":task_result_dict.get("reason") or task_result_dict.get("output"),
                "result_summary":task_result_dict.get("output", "")[:250],
                "completed_at":datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            }
            current_task_status.update(current_task_status_update) #type: ignore
            logger.info(f"DAG {dag_id}: Task '{task_id}' completed with status: {final_status_str}")
        except Exception as e:
            logger.error(f"DAG {dag_id}: Exception during task '{task_id}': {e}", exc_info=True)
            current_task_status.update(status='FAILED', message=str(e), completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z") #type: ignore
        finally:
            dag_state["task_statuses"][task_id] = current_task_status # Ensure updated status is stored
            self._publish_task_status(project_id, dag_id, current_task_status)
            dag_state["running_tasks"].remove(task_id)
            dag_state["completed_tasks"].add(task_id)

    async def _process_dag_execution_cycle(self, project_id: Optional[str], dag_id: str):
        # ... (as in response #61, ensure it calls the updated _publish_dag_status) ...
        dag_state = self.active_dags.get(dag_id)
        if not dag_state: logger.error(f"Cannot process cycle for DAG {dag_id}: Not found."); return False # Return bool
        node_map: Dict[str, DagNode] = dag_state["node_map"]
        in_degree: Dict[str, int] = dag_state["in_degree"]
        adj: Dict[str, List[str]] = dag_state["adj"]
        task_statuses: Dict[str, TaskStatus] = dag_state["task_statuses"]
        completed_tasks: Set[str] = dag_state["completed_tasks"]
        running_tasks: Set[str] = dag_state["running_tasks"]

        runnable_tasks_this_cycle = [
            task_id for task_id, degree in list(in_degree.items()) 
            if degree == 0 and task_id not in completed_tasks and task_id not in running_tasks
        ]
        for task_id_to_run in runnable_tasks_this_cycle:
            running_tasks.add(task_id_to_run)
            asyncio.create_task(self.execute_dag_task(project_id, dag_id, task_id_to_run))

        if len(completed_tasks) == len(node_map):
            all_task_status_objects = list(task_statuses.values())
            all_successful = all(ts.get("status") == "SUCCESS" for ts in all_task_status_objects if ts.get("task_id") in completed_tasks)
            final_dag_status = "COMPLETED_SUCCESS" if all_successful else "COMPLETED_PARTIAL"
            if any(ts.get("status") == "FAILED" for ts in all_task_status_objects): final_dag_status = "FAILED"
            logger.info(f"DAG {dag_id} processing complete. Final status: {final_dag_status}")
            self._publish_dag_status(project_id, dag_id, final_dag_status, all_task_status_objects) # This will now also store
            if dag_id in self.active_dags: del self.active_dags[dag_id]
            return True 

        if any(ts.get("status") == "FAILED" for ts in task_statuses.values()):
            logger.error(f"DAG {dag_id} has failed tasks. Halting.")
            self._publish_dag_status(project_id, dag_id, "FAILED", list(task_statuses.values()), "One or more tasks failed.") # This will now also store
            if dag_id in self.active_dags: del self.active_dags[dag_id]
            return True 
        return False 

    async def execute_dag_orchestrator(self, project_id: Optional[str], dag: DagDefinition):
        # ... (as in response #61, ensures _process_dag_execution_cycle is awaited correctly) ...
        dag_id = dag['dag_id']
        span = self._start_trace_span_if_available("execute_dag_orchestrator", dag_id=dag_id, project_id=project_id or "N/A")
        logger.info(f"Orchestrating execution for DAG ID: {dag_id}, Project: {project_id}")
        nodes_list: List[DagNode] = dag['nodes']
        node_map: Dict[str, DagNode] = {node['id']: node for node in nodes_list}
        adj: Dict[str, List[str]] = defaultdict(list); in_degree: Dict[str, int] = {node['id']: 0 for node in nodes_list}
        for node_id, node_data in node_map.items():
            for dep_id in node_data.get('dependencies', []):
                if dep_id in node_map: adj[dep_id].append(node_id); in_degree[node_id] += 1
                else: logger.error(f"DAG {dag_id}: Task '{node_id}' has unknown dependency '{dep_id}'.")

        self.active_dags[dag_id] = {
            "dag": dag, "node_map": node_map, "adj": adj, "in_degree": in_degree,
            "task_statuses": {node['id']: TaskStatus(task_id=node['id'], status='PENDING', message="Awaiting dependencies") for node in nodes_list}, #type: ignore
            "completed_tasks": set(), "running_tasks": set(),
            "started_at": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        }
        self._publish_dag_status(project_id, dag_id, "STARTED", list(self.active_dags[dag_id]["task_statuses"].values()))

        try:
            with span: #type: ignore
                while dag_id in self.active_dags: # Check if DAG is still active (not deleted by completion/failure)
                    dag_finished = await self._process_dag_execution_cycle(project_id, dag_id)
                    if dag_finished:
                        if _tracer and _trace_api: span.set_attribute("plan_agent.dag_final_status", self.active_dags.get(dag_id, {}).get("final_status", "UNKNOWN_IN_TRACE"))
                        break
                    await asyncio.sleep(1) 
                if _tracer and _trace_api: span.set_status(trace.StatusCode.OK)
        except Exception as e:
            logger.error(f"Critical error during DAG orchestration {dag_id}: {e}", exc_info=True)
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(trace.StatusCode.ERROR)
            final_statuses = list(self.active_dags.get(dag_id, {}).get("task_statuses", {}).values())
            self._publish_dag_status(project_id, dag_id, "FAILED", final_statuses, message=f"Orchestration error: {e}")
        finally:
            if dag_id in self.active_dags: 
                logger.info(f"Cleaning up active DAG {dag_id} post-orchestration (if not already).")
                # Publish final status if not already published by _process_dag_execution_cycle due to error path
                if not any(s.startswith("COMPLETED") or s == "FAILED" for s in [ts.get("status", "") for ts in self.active_dags[dag_id]["task_statuses"].values()]): #type: ignore
                    self._publish_dag_status(project_id, dag_id, "UNKNOWN_FAILURE", list(self.active_dags[dag_id]["task_statuses"].values()), "Orchestration ended unexpectedly")
                del self.active_dags[dag_id]


    async def handle_dag_definition_event(self, event_data_str: str):
        # ... (logic from response #61, calls execute_dag_orchestrator via asyncio.create_task) ...
        # This method should remain the same, ensuring it's fully async and uses the tracer.
        span = self._start_trace_span_if_available("handle_dag_definition_event")
        try:
            with span: # type: ignore
                event_data: DagDefinitionCreatedEvent = json.loads(event_data_str) #type: ignore
                if not (event_data.get("event_type") == "DagDefinitionCreatedEvent" and "dag" in event_data and "dag_id" in event_data["dag"]):
                    logger.error(f"Malformed DagDefinitionCreatedEvent: {event_data_str[:200]}"); return
                dag_to_execute = event_data["dag"]
                project_id = event_data.get("project_id")
                logger.info(f"PlanAgent handling DagDefinitionCreatedEvent for DAG: {dag_to_execute['dag_id']}, Project: {project_id}")
                if _tracer: span.set_attributes({"dag_id": dag_to_execute['dag_id'], "project_id": project_id})
                asyncio.create_task(self.execute_dag_orchestrator(project_id, dag_to_execute))
        except Exception as e:
            logger.error(f"Error handling DagDefinitionCreatedEvent: {e}", exc_info=True)
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(trace.StatusCode.ERROR)

    async def main_event_loop(self):
        # ... (logic from response #61, subscribes to DAG_DEFINITION_EVENT_CHANNEL_PATTERN) ...
        # This method should remain the same, ensuring it's fully async and uses the tracer for event processing.
        if not self.event_bus.redis_client: logger.critical("PlanAgent: EventBus disconnected. Exiting."); await asyncio.sleep(60); return
        pubsub = self.event_bus.subscribe_to_channel(DAG_DEFINITION_EVENT_CHANNEL_PATTERN)
        if not pubsub: logger.critical(f"PlanAgent: Failed to subscribe. Exiting."); await asyncio.sleep(60); return
        logger.info(f"PlanAgent worker (V0.2) subscribed to '{DAG_DEFINITION_EVENT_CHANNEL_PATTERN}', listening...")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0) #type: ignore
                if message and message["type"] == "pmessage":
                    parent_span_context = None # Extract from message if available
                    span = self._start_trace_span_if_available("process_dag_definition_bus_event", parent_context=parent_span_context)
                    try:
                        with span: #type: ignore
                            if _tracer: span.set_attribute("messaging.source.channel", message.get("channel"))
                            await self.handle_dag_definition_event(message["data"]) #type: ignore
                    finally:
                        if hasattr(span, 'end'): span.end() #type: ignore
                await asyncio.sleep(0.01) 
        except KeyboardInterrupt: logger.info("PlanAgent event loop interrupted.")
        except Exception as e: logger.error(f"Critical error in PlanAgent event loop: {e}", exc_info=True)
        finally: # ... (pubsub cleanup from response #61)
            logger.info("PlanAgent shutting down...");
            if pubsub:
                try: await asyncio.to_thread(pubsub.punsubscribe, DAG_DEFINITION_EVENT_CHANNEL_PATTERN); await asyncio.to_thread(pubsub.close) #type: ignore
                except: pass
            logger.info("PlanAgent shutdown complete.")


async def main_async_runner(): # From response #61
    agent = PlanAgent()
    await agent.main_event
# In agents/PlanAgent/app/agent.py, within the PlanAgent class:
# Add ForgeIQClient to its __init__ if not already there for other purposes.
# For this example, assume it gets an SDK client instance.

# def __init__(self, event_bus: EventBus, shared_memory: SharedMemoryStore, sdk_client: ForgeIQClient):
#     # ...
#     self.sdk_client = sdk_client

async def request_build_strategy_optimization(self, project_id: str, current_dag_def: DagDefinition) -> Optional[SDKOptimizedAlgorithmResponse]:
    """Requests the AlgorithmAgent (via ForgeIQ-backend) to optimize a build strategy."""
    if not self.sdk_client: # Assuming PlanAgent has self.sdk_client
        logger.error(f"PlanAgent: ForgeIQ SDK client not available. Cannot request build strategy optimization for {project_id}.")
        return None

    logger.info(f"PlanAgent: Requesting build strategy optimization for project '{project_id}'.")
    span = self._start_trace_span_if_available("request_build_strategy_optimization", project_id=project_id, dag_id=current_dag_def['dag_id'])

    try:
        with span: #type: ignore
            # Prepare context for the AlgorithmAgent.
            # The 'dag_representation' for AlgorithmContext could be the nodes list,
            # or another serialized form of the DAG.
            # Your AlgorithmAgent's prompt expects context['dag'].
            sdk_context = SDKAlgorithmContext(
                project_id=project_id,
                dag_representation=current_dag_def.get("nodes", []), # Send nodes list as DAG representation
                telemetry_data={"source_agent": "PlanAgent", "current_task_count": len(current_dag_def.get("nodes", []))}
            )

            optimized_strategy = await self.sdk_client.request_build_strategy_optimization(context=sdk_context)

            if optimized_strategy:
                logger.info(f"PlanAgent: Received optimized strategy for project '{project_id}': Ref {optimized_strategy.get('algorithm_reference')}")
                if _trace_api and span: span.set_attribute("optimization.ref", optimized_strategy.get('algorithm_reference')); span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                # TODO: PlanAgent would then potentially use this optimized_strategy.generated_code_or_dag
                # (which might be a new DagDefinition) for subsequent execution.
                return optimized_strategy
            else:
                logger.warning(f"PlanAgent: No optimized strategy returned for project '{project_id}'.")
                if _trace_api and span: span.set_attribute("optimization.received", False)
                return None
    except Exception as e:
        logger.error(f"PlanAgent: Error requesting build strategy optimization for project '{project_id}': {e}", exc_info=True)
        if _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR))
        return None
