# ====================================
# 📁 agents/PlanAgent/agent.py
# ====================================
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
tracer = None
try:
    from opentelemetry import trace
    from core.observability.tracing import setup_tracing
    tracer = setup_tracing(SERVICE_NAME)
except ImportError:
    logging.getLogger(SERVICE_NAME).warning("PlanAgent: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from core.task_runner import run_task
from core.shared_memory import SharedMemoryStore # <<< IMPORT SharedMemoryStore
from interfaces.types.events import (
    DagDefinition, DagNode, DagDefinitionCreatedEvent,
    TaskStatus, TaskStatusUpdateEvent, DagExecutionStatusEvent
)

# Event channels (as before)
DAG_DEFINITION_EVENT_CHANNEL_PATTERN = "events.project.*.dag.created"
TASK_STATUS_EVENT_CHANNEL_TEMPLATE = "events.project.{project_id}.dag.{dag_id}.task_status"
DAG_EXECUTION_STATUS_CHANNEL_TEMPLATE = "events.project.{project_id}.dag.{dag_id}.execution_status"

# Shared Memory Key for DAG status
DAG_STATUS_SUMMARY_KEY_TEMPLATE = "dag_execution:{dag_id}:status_summary" # Key ForgeIQ-backend will query

class PlanAgent:
    def __init__(self):
        logger.info("Initializing PlanAgent (with SharedMemory for status)...")
        self.event_bus = EventBus()
        self.shared_memory = SharedMemoryStore() # <<< INITIALIZE SharedMemoryStore
        self.active_dags: Dict[str, Dict[str, Any]] = {}
        if not self.event_bus.redis_client:
            logger.error("PlanAgent: EventBus not connected.")
        if not self.shared_memory.redis_client:
            logger.warning("PlanAgent: SharedMemoryStore not connected to Redis. Will not persist final DAG status.")
        logger.info("PlanAgent Initialized.")

    @property
    def tracer_instance(self):
        return tracer

    async def _store_final_dag_status(self, dag_id: str, status_event_data: DagExecutionStatusEvent):
        """Stores the final DAG execution status summary in SharedMemoryStore."""
        if not self.shared_memory.redis_client:
            logger.warning(f"DAG {dag_id}: Cannot store final status, SharedMemoryStore not connected.")
            return

        status_key = DAG_STATUS_SUMMARY_KEY_TEMPLATE.format(dag_id=dag_id)
        # The status_event_data is already a TypedDict matching SDKDagExecutionStatusModel closely
        # We can store the whole event or a tailored summary. Let's store the event data.
        try:
            success = await self.shared_memory.set_value(
                status_key, 
                status_event_data, # This is a dict
                expiry_seconds=7 * 24 * 60 * 60 # Store for 7 days, for example
            )
            if success:
                logger.info(f"DAG {dag_id}: Final status summary stored to SharedMemory key '{status_key}'.")
            else:
                logger.error(f"DAG {dag_id}: Failed to store final status summary to SharedMemory for key '{status_key}'.")
        except Exception as e:
            logger.error(f"DAG {dag_id}: Exception storing final status to SharedMemory: {e}", exc_info=True)


    def _publish_dag_status(self, project_id: Optional[str], dag_id: str, status: str, 
                            all_task_statuses: List[TaskStatus], message: Optional[str]=None):
        # ... (event construction as before) ...
        dag_info = self.active_dags.get(dag_id, {})
        start_time = dag_info.get("started_at", datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z")
        completion_time = None
        is_terminal_status = "COMPLETED" in status.upper() or "FAILED" in status.upper()
        if is_terminal_status:
            completion_time = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"

        event_data = DagExecutionStatusEvent(
            event_type="DagExecutionStatusEvent",
            project_id=project_id, dag_id=dag_id, status=status, message=message,
            started_at=start_time, completed_at=completion_time,
            task_statuses=all_task_statuses,
            timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        )
        
        if self.event_bus.redis_client:
            channel = DAG_EXECUTION_STATUS_CHANNEL_TEMPLATE.format(project_id=project_id or "global", dag_id=dag_id)
            self.event_bus.publish(channel, event_data)
            logger.info(f"Published DagExecutionStatusEvent to {channel}: DAG {dag_id} status {status}")

        # If it's a terminal status, also store it in SharedMemory
        if is_terminal_status:
            # We need to run this async task without blocking the publisher
            asyncio.create_task(self._store_final_dag_status(dag_id, event_data))


    # ... (rest of PlanAgent class: _publish_task_status, execute_dag_task, 
    #      _process_dag_execution_cycle, execute_dag_orchestrator, 
    #      handle_dag_definition_event, main_event_loop as defined in response #61) ...
    # Ensure all these methods correctly use logger, self.tracer_instance, etc.
    # I will re-paste the full class structure from response #61 with the modifications integrated.
    
    async def _publish_task_status(self, project_id: Optional[str], dag_id: str, task_status_data: TaskStatus): # Corrected definition from #61
        if self.event_bus.redis_client:
            event_data = TaskStatusUpdateEvent(
                event_type="TaskStatusUpdateEvent", project_id=project_id, dag_id=dag_id,
                task=task_status_data,
                timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            )
            channel = TASK_STATUS_EVENT_CHANNEL_TEMPLATE.format(project_id=project_id or "global", dag_id=dag_id)
            self.event_bus.publish(channel, event_data)
            logger.debug(f"Published TaskStatusUpdateEvent to {channel}: {task_status_data}")

    async def execute_dag_task(self, project_id: Optional[str], dag_id: str, task_id: str):
        dag_state = self.active_dags.get(dag_id)
        if not dag_state: logger.error(f"DAG {dag_id} not found. Cannot execute task {task_id}."); return
        node_map: Dict[str, DagNode] = dag_state["node_map"]
        task_def = node_map.get(task_id)
        if not task_def:
            logger.error(f"Task definition for {task_id} not found in DAG {dag_id}.")
            dag_state["task_statuses"][task_id] = TaskStatus(task_id=task_id, status='FAILED', message="Task definition not found")
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
            current_task_status.update(
                status=final_status_str, message=task_result_dict.get("reason") or task_result_dict.get("output"),
                result_summary=task_result_dict.get("output", "")[:250],
                completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            )
            logger.info(f"DAG {dag_id}: Task '{task_id}' completed with status: {final_status_str}")
        except Exception as e:
            logger.error(f"DAG {dag_id}: Exception during task '{task_id}': {e}", exc_info=True)
            current_task_status.update(status='FAILED', message=str(e), completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z")
        finally:
            dag_state["task_statuses"][task_id] = current_task_status
            self._publish_task_status(project_id, dag_id, current_task_status)
            dag_state["running_tasks"].remove(task_id)
            dag_state["completed_tasks"].add(task_id)

    async def _process_dag_execution_cycle(self, project_id: Optional[str], dag_id: str):
        dag_state = self.active_dags.get(dag_id)
        if not dag_state: logger.error(f"Cannot process cycle for DAG {dag_id}: Not found."); return
        node_map: Dict[str, DagNode] = dag_state["node_map"]
        in_degree: Dict[str, int] = dag_state["in_degree"]
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
            all_successful = all(ts.get("status") == "SUCCESS" for ts in task_statuses.values() if ts.get("task_id") in completed_tasks) # Note: core.task_runner returns "success"
            final_dag_status = "COMPLETED_SUCCESS" if all_successful else "COMPLETED_PARTIAL"
            if any(ts.get("status") == "FAILED" for ts in task_statuses.values()): final_dag_status = "FAILED"
            logger.info(f"DAG {dag_id} processing complete. Final status: {final_dag_status}")
            self._publish_dag_status(project_id, dag_id, final_dag_status, list(task_statuses.values()))
            if dag_id in self.active_dags: del self.active_dags[dag_id]
            return True # DAG finished

        if any(ts.get("status") == "FAILED" for ts in task_statuses.values()):
            logger.error(f"DAG {dag_id} has failed tasks. Halting.")
            self._publish_dag_status(project_id, dag_id, "FAILED", list(task_statuses.values()), "One or more tasks failed.")
            if dag_id in self.active_dags: del self.active_dags[dag_id]
            return True # DAG finished (due to failure)
        return False # DAG still ongoing

    async def execute_dag_orchestrator(self, project_id: Optional[str], dag: DagDefinition):
        dag_id = dag['dag_id']
        span = self._start_trace_span_if_available("execute_dag_orchestrator", dag_id=dag_id, project_id=project_id or "N/A")
        logger.info(f"Orchestrating execution for DAG ID: {dag_id}, Project: {project_id}")
        
        nodes_list: List[DagNode] = dag['nodes']
        node_map: Dict[str, DagNode] = {node['id']: node for node in nodes_list}
        adj: Dict[str, List[str]] = defaultdict(list)
        in_degree: Dict[str, int] = {node['id']: 0 for node in nodes_list}
        for node_id, node_data in node_map.items():
            for dep_id in node_data.get('dependencies', []):
                if dep_id in node_map: adj[dep_id].append(node_id); in_degree[node_id] += 1
                else: logger.error(f"DAG {dag_id}: Task '{node_id}' has unknown dependency '{dep_id}'.")
        
        self.active_dags[dag_id] = {
            "dag": dag, "node_map": node_map, "adj": adj, "in_degree": in_degree,
            "task_statuses": {node['id']: TaskStatus(task_id=node['id'], status='PENDING', message="Awaiting dependencies") for node in nodes_list},
            "completed_tasks": set(), "running_tasks": set(),
            "started_at": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        }
        self._publish_dag_status(project_id, dag_id, "STARTED", list(self.active_dags[dag_id]["task_statuses"].values()))
        
        try:
            with span: #type: ignore
                while True:
                    dag_finished = await self._process_dag_execution_cycle(project_id, dag_id)
                    if dag_finished:
                        if _tracer and _trace_api: span.set_attribute("plan_agent.dag_final_status", self.active_dags.get(dag_id, {}).get("final_status", "UNKNOWN_IN_TRACE"))
                        break
                    await asyncio.sleep(1) 
                if _tracer and _trace_api: span.set_status(trace.StatusCode.OK)
        except Exception as e:
            logger.error(f"Critical error during DAG orchestration {dag_id}: {e}", exc_info=True)
            if _tracer and _trace_api: span.record_exception(e); span.set_status(trace.StatusCode.ERROR)
            final_statuses = list(self.active_dags.get(dag_id, {}).get("task_statuses", {}).values())
            self._publish_dag_status(project_id, dag_id, "FAILED", final_statuses, message=f"Orchestration error: {e}")
        finally:
            if dag_id in self.active_dags: 
                logger.info(f"Cleaning up active DAG {dag_id} post-orchestration.")
                del self.active_dags[dag_id]

    async def handle_dag_definition_event(self, event_data_str: str):
        # ... (logic from response #61, calls execute_dag_orchestrator via asyncio.create_task) ...
        logger.debug(f"PlanAgent received raw event data: {message_summary(event_data_str)}")
        try:
            event: DagDefinitionCreatedEvent = json.loads(event_data_str) #type: ignore
            if not (event.get("event_type") == "DagDefinitionCreatedEvent" and "dag" in event and "dag_id" in event["dag"]):
                logger.error(f"Malformed DagDefinitionCreatedEvent: {event_data_str[:200]}"); return
            dag_to_execute = event["dag"]
            project_id = event.get("project_id")
            logger.info(f"PlanAgent handling DagDefinitionCreatedEvent for DAG: {dag_to_execute['dag_id']}, Project: {project_id}")
            asyncio.create_task(self.execute_dag_orchestrator(project_id, dag_to_execute))
        except json.JSONDecodeError: logger.error(f"Could not decode JSON from DagDefinitionCreatedEvent: {event_data_str[:200]}")
        except Exception as e: logger.error(f"Error handling DagDefinitionCreatedEvent: {e}", exc_info=True)

    async def main_event_loop(self):
        # ... (logic from response #61, subscribes to DAG_DEFINITION_EVENT_CHANNEL_PATTERN) ...
        if not self.event_bus.redis_client: logger.critical("PlanAgent: EventBus not connected. Exiting."); await asyncio.sleep(60); return
        pubsub = self.event_bus.subscribe_to_channel(DAG_DEFINITION_EVENT_CHANNEL_PATTERN)
        if not pubsub: logger.critical(f"PlanAgent: Failed to subscribe. Exiting."); await asyncio.sleep(60); return
        logger.info(f"PlanAgent worker subscribed to '{DAG_DEFINITION_EVENT_CHANNEL_PATTERN}', listening for DAGs...")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "pmessage":
                    span_name = "plan_agent.process_dag_definition_event_from_bus"
                    if not self.tracer_instance: await self.handle_dag_definition_event(message["data"])
                    else:
                        with self.tracer_instance.start_as_current_span(span_name) as span:
                            span.set_attributes({"messaging.system": "redis", "messaging.destination.name": message.get("channel")})
                            await self.handle_dag_definition_event(message["data"])
                await asyncio.sleep(0.01) 
        except KeyboardInterrupt: logger.info("PlanAgent event loop interrupted.")
        except Exception as e: logger.error(f"Critical error in PlanAgent event loop: {e}", exc_info=True)
        finally:
            logger.info("PlanAgent shutting down pubsub...");
            if pubsub:
                try: 
                    await asyncio.to_thread(pubsub.punsubscribe, DAG_DEFINITION_EVENT_CHANNEL_PATTERN)
                    await asyncio.to_thread(pubsub.close)
                except Exception as e_close: logger.error(f"Error closing pubsub for PlanAgent: {e_close}")
            logger.info("PlanAgent shutdown complete.")

async def main_async_runner():
    agent = PlanAgent()
    await agent.main_event_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main_async_runner())
    except KeyboardInterrupt:
        logger.info("PlanAgent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"PlanAgent failed to start or unhandled error: {e}", exc_info=True)
