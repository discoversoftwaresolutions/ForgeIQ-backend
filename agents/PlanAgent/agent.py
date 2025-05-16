# agents/PlanAgent/agent.py
import os
import json
import datetime
import uuid
import asyncio
import logging
from collections import defaultdict
from typing import Dict, Any, Optional, List, Set

# --- Observability Setup ---
SERVICE_NAME = "PlanAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s'
)
tracer = None
try:
    from core.observability.tracing import setup_tracing
    tracer = setup_tracing(SERVICE_NAME)
except ImportError:
    logging.getLogger(SERVICE_NAME).warning(
        "PlanAgent: Tracing setup failed. Ensure core.observability.tracing is available."
    )
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
# Assuming your core modules are importable like this due to PYTHONPATH=/app
from core.task_runner import run_task # Using the run_task from user's provided core.task_runner
from core.build_graph import get_project_dag # This might be used for context or validation
                                             # but BuildSurfAgent provides the primary DAG structure.

from interfaces.types.events import (
    DagDefinition, DagNode, DagDefinitionCreatedEvent,
    TaskStatus, TaskStatusUpdateEvent, DagExecutionStatusEvent
)

# Event channels
DAG_DEFINITION_EVENT_CHANNEL_PATTERN = "events.project.*.dag.created" # Listens for DAGs
TASK_STATUS_EVENT_CHANNEL_TEMPLATE = "events.project.{project_id}.dag.{dag_id}.task_status"
DAG_EXECUTION_STATUS_CHANNEL_TEMPLATE = "events.project.{project_id}.dag.{dag_id}.execution_status"

class PlanAgent:
    def __init__(self):
        logger.info("Initializing PlanAgent...")
        self.event_bus = EventBus()
        self.active_dags: Dict[str, Dict[str, Any]] = {} # Store active DAGs being processed
        if not self.event_bus.redis_client:
            logger.error("PlanAgent critical: EventBus not connected. Cannot process DAGs.")
        logger.info("PlanAgent Initialized.")

    @property
    def tracer(self):
        return tracer

    def _publish_task_status(self, project_id: Optional[str], dag_id: str, task_status: TaskStatus):
        if self.event_bus.redis_client:
            event_data = TaskStatusUpdateEvent(
                event_type="TaskStatusUpdateEvent",
                project_id=project_id,
                dag_id=dag_id,
                task=task_status,
                timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            )
            channel = TASK_STATUS_EVENT_CHANNEL_TEMPLATE.format(project_id=project_id or "global", dag_id=dag_id)
            self.event_bus.publish(channel, event_data)

    def _publish_dag_status(self, project_id: Optional[str], dag_id: str, status: str, task_statuses: List[TaskStatus], message: Optional[str]=None):
        if self.event_bus.redis_client:
            dag_info = self.active_dags.get(dag_id, {})
            event_data = DagExecutionStatusEvent(
                event_type="DagExecutionStatusEvent",
                project_id=project_id,
                dag_id=dag_id,
                status=status,
                message=message,
                started_at=dag_info.get("started_at", datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"),
                completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z" if "COMPLETED" in status or "FAILED" in status else None,
                task_statuses=task_statuses,
                timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            )
            channel = DAG_EXECUTION_STATUS_CHANNEL_TEMPLATE.format(project_id=project_id or "global", dag_id=dag_id)
            self.event_bus.publish(channel, event_data)


    async def execute_dag(self, project_id: Optional[str], dag: DagDefinition):
        dag_id = dag['dag_id']
        span_name = f"execute_dag:{dag_id}"
        parent_span_context = None # Extract from triggering event if possible

        if not self.tracer: # Fallback
            await self._execute_dag_logic(project_id, dag)
            return

        with self.tracer.start_as_current_span(span_name, context=parent_span_context) as span:
            span.set_attributes({
                "plan_agent.dag_id": dag_id,
                "plan_agent.project_id": project_id or "N/A",
                "plan_agent.node_count": len(dag.get("nodes", []))
            })
            logger.info(f"Starting execution for DAG ID: {dag_id}, Project: {project_id}")
            self.active_dags[dag_id] = {
                "dag": dag,
                "task_statuses": {node['id']: TaskStatus(task_id=node['id'], status='PENDING') for node in dag['nodes']},
                "completed_tasks": set(),
                "running_tasks": set(),
                "started_at": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            }
            self._publish_dag_status(project_id, dag_id, "STARTED", list(self.active_dags[dag_id]["task_statuses"].values()))

            try:
                await self._execute_dag_logic(project_id, dag)
                # Final status will be published within _execute_dag_logic
                span.set_status(trace.StatusCode.OK) # Assuming success if no exception bubbles up
            except Exception as e:
                logger.error(f"Critical error during DAG execution {dag_id}: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, f"DAG execution failed: {e}"))
                # Ensure a final FAILED status is published if an unhandled exception occurs
                final_task_statuses = list(self.active_dags.get(dag_id, {}).get("task_statuses", {}).values())
                self._publish_dag_status(project_id, dag_id, "FAILED", final_task_statuses, message=str(e))
            finally:
                if dag_id in self.active_dags: # Cleanup
                    del self.active_dags[dag_id]


    async def _execute_dag_logic(self, project_id: Optional[str], dag: DagDefinition):
        dag_id = dag['dag_id']
        nodes: List[DagNode] = dag['nodes']
        node_map: Dict[str, DagNode] = {node['id']: node for node in nodes}

        # Build adjacency list and in-degree map for topological sort / dependency tracking
        adj: Dict[str, List[str]] = defaultdict(list)
        in_degree: Dict[str, int] = {node['id']: 0 for node in nodes}

        for node_id, node_data in node_map.items():
            for dep_id in node_data.get('dependencies', []):
                adj[dep_id].append(node_id) # dep_id -> node_id means dep_id must run before node_id
                in_degree[node_id] += 1

        # Queue for nodes ready to run (in-degree is 0)
        queue: asyncio.Queue[str] = asyncio.Queue()
        for node_id in in_degree:
            if in_degree[node_id] == 0:
                await queue.put(node_id)

        dag_status_info = self.active_dags[dag_id]

        while not queue.empty() or dag_status_info["running_tasks"]:
            # Launch new tasks if any are ready and concurrency limits allow (not implemented here)
            while not queue.empty():
                task_id_to_run = await queue.get()
                dag_status_info["running_tasks"].add(task_id_to_run)

                task_def = node_map[task_id_to_run]
                logger.info(f"DAG {dag_id}: Submitting task '{task_id_to_run}' ({task_def['task_type']}) for execution.")

                task_status_obj = TaskStatus(
                    task_id=task_id_to_run, status='RUNNING', 
                    started_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                )
                dag_status_info["task_statuses"][task_id_to_run] = task_status_obj
                self._publish_task_status(project_id, dag_id, task_status_obj)

                # Execute the task asynchronously
                # The run_task function from core.task_runner is synchronous.
                # To make this truly non-blocking, run_task would need to be async
                # or run in a thread pool executor.
                # For now, we'll await it if it were async, or run it in a thread.
                # Let's assume run_task is synchronous and we run it in executor.
                loop = asyncio.get_running_loop()

                # Ensure project_id or a suitable project context is passed to run_task if needed.
                # Your core.task_runner.run_task takes `project` as an argument.
                # We need to ensure it's passed correctly. `BuildSurfAgent` may or may not put project_id in DAG itself.
                # Using the overall project_id for now.
                task_execution_project_context = project_id or dag.get("project_id", "default_project")

                # Using user's provided core.task_runner.run_task
                # It expects task (string, e.g. "lint") and project (string)
                # The DAGNode has task_type, command, agent_handler, params.
                # We need to map this to what core.task_runner.run_task expects.
                # For this V0.1, if command is present, we use it. Otherwise, task_type.
                task_name_for_runner = task_def['task_type']
                if task_def.get('command'): # If a specific command list is given
                    # This part needs refinement: core.task_runner.TASK_COMMANDS uses predefined commands.
                    # If DAG node provides its own command, task_runner might need to execute it directly.
                    # For now, let's assume task_type maps to a command in task_runner.
                    # Or, if task_type is 'custom_script' and command is provided, task_runner needs to handle that.
                    # This is a gap: the DAG from BuildSurf might be too generic for the current core.task_runner.
                    # For now, just pass task_type.
                    logger.warning(f"DAG {dag_id}: Task '{task_id_to_run}' has a command: {task_def['command']}. "
                                   f"Current task_runner uses predefined commands for task_type: '{task_name_for_runner}'. "
                                   "Refinement needed for custom commands.")

                # We will use asyncio.to_thread to run the synchronous `run_task`
                task_result_dict = await loop.run_in_executor(
                    None,  # Use default ThreadPoolExecutor
                    run_task, # This is from your core.task_runner
                    task_name_for_runner, # Task name (e.g. "lint", "build")
                    task_execution_project_context # Project context string
                )

                # Process task result
                dag_status_info["running_tasks"].remove(task_id_to_run)
                dag_status_info["completed_tasks"].add(task_id_to_run)

                final_status = task_result_dict.get("status", "error").upper() # success -> SUCCESS
                task_status_obj = TaskStatus(
                    task_id=task_id_to_run, status=final_status,
                    message=task_result_dict.get("reason") or task_result_dict.get("output"), # Simplified
                    result_summary=task_result_dict.get("output", "")[:200], # Short summary
                    started_at=dag_status_info["task_statuses"][task_id_to_run].get("started_at"),
                    completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                )
                dag_status_info["task_statuses"][task_id_to_run] = task_status_obj
                self._publish_task_status(project_id, dag_id, task_status_obj)

                if final_status == "SUCCESS":
                    for neighbor_id in adj[task_id_to_run]:
                        in_degree[neighbor_id] -= 1
                        if in_degree[neighbor_id] == 0:
                            await queue.put(neighbor_id)
                else: # Task FAILED or SKIPPED due to unknown task
                    logger.error(f"DAG {dag_id}: Task '{task_id_to_run}' failed or was skipped. Stopping DAG execution.")
                    self._publish_dag_status(project_id, dag_id, "FAILED", list(dag_status_info["task_statuses"].values()), 
                                             message=f"Task '{task_id_to_run}' failed.")
                    return # Stop processing this DAG

            if not dag_status_info["running_tasks"] and queue.empty(): # No more tasks to run or running
                break

            await asyncio.sleep(0.1) # Yield control to allow other tasks / event loop activities

        # All tasks completed successfully
        if len(dag_status_info["completed_tasks"]) == len(nodes):
            logger.info(f"DAG {dag_id}: All tasks completed successfully.")
            self._publish_dag_status(project_id, dag_id, "COMPLETED_SUCCESS", list(dag_status_info["task_statuses"].values()))
        else:
            # This case should ideally be caught earlier if a task fails
            logger.warning(f"DAG {dag_id}: Exited loop but not all tasks completed. Status may be inconsistent.")
            # Check if any tasks are still PENDING or RUNNING (should not happen if logic is correct)
            # Publish a PARTIAL or FAILED status based on final states.
            final_statuses = list(dag_status_info["task_statuses"].values())
            if any(ts['status'] == 'FAILED' for ts in final_statuses):
                 self._publish_dag_status(project_id, dag_id, "FAILED", final_statuses, message="One or more tasks failed.")
            elif any(ts['status'] in ['PENDING', 'RUNNING'] for ts in final_statuses):
                 self._publish_dag_status(project_id, dag_id, "COMPLETED_PARTIAL", final_statuses, message="DAG execution ended with incomplete tasks.")
            else: # Should be success if all completed and none failed
                 self._publish_dag_status(project_id, dag_id, "COMPLETED_SUCCESS", final_statuses)


    async def handle_dag_definition_event(self, event_data_str: str):
        span_name = "handle_dag_definition_event"
        parent_context = None 

        if not self.tracer: # Fallback
            await self._handle_dag_definition_event_logic(event_data_str)
            return

        with self.tracer.start_as_current_span(span_name, context=parent_context) as span:
            try:
                event_data: DagDefinitionCreatedEvent = json.loads(event_data_str)
                if not (event_data.get("event_type") == "DagDefinitionCreatedEvent" and 
                        "dag" in event_data and "dag_id" in event_data["dag"]):
                    logger.error(f"Malformed DagDefinitionCreatedEvent: {event_data_str[:200]}")
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "Malformed event"))
                    return

                dag_to_execute = event_data["dag"]
                project_id = event_data.get("project_id")
                span.set_attributes({
                    "messaging.system": "redis",
                    "plan_agent.dag_id": dag_to_execute["dag_id"],
                    "plan_agent.project_id": project_id or "N/A",
                    "plan_agent.request_id": event_data.get("request_id")
                })
                logger.info(f"PlanAgent handling DagDefinitionCreatedEvent for DAG: {dag_to_execute['dag_id']}")

                # Schedule DAG execution (don't block the event listener)
                asyncio.create_task(self.execute_dag(project_id, dag_to_execute))

            except json.JSONDecodeError:
                logger.error(f"Could not decode JSON from event: {event_data_str[:200]}")
                span.set_status(trace.Status(trace.StatusCode.ERROR, "JSON decode error"))
            except Exception as e:
                logger.error(f"Error handling DagDefinitionCreatedEvent: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Event handling failed"))


    async def main_event_loop(self):
        if not self.event_bus.redis_client:
            logger.critical("PlanAgent: Cannot start, EventBus not connected.")
            return

        pubsub = self.event_bus.subscribe_to_channel(DAG_DEFINITION_EVENT_CHANNEL_PATTERN)
        if not pubsub:
            logger.critical(f"PlanAgent: Failed to subscribe to {DAG_DEFINITION_EVENT_CHANNEL_PATTERN}. Worker cannot start.")
            return

        logger.info(f"PlanAgent worker subscribed to {DAG_DEFINITION_EVENT_CHANNEL_PATTERN}, listening for DAGs...")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "pmessage": # pmessage for pattern subscriptions
                    await self.handle_dag_definition_event(message["data"])
                await asyncio.sleep(0.01) 
        except KeyboardInterrupt:
            logger.info("PlanAgent event loop interrupted.")
        except Exception as e:
            logger.error(f"Critical error in PlanAgent event loop: {e}", exc_info=True)
        finally:
            logger.info("PlanAgent shutting down pubsub...")
            if pubsub:
                try: await asyncio.to_thread(pubsub.punsubscribe, DAG_DEFINITION_EVENT_CHANNEL_PATTERN)
                except: pass
                try: await asyncio.to_thread(pubsub.close)
                except: pass
            logger.info("PlanAgent shutdown complete.")

async def main():
    agent = PlanAgent()
    await agent.main_event_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("PlanAgent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"PlanAgent failed to start or unhandled error in main: {e}", exc_info=True)
