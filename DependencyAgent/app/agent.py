# =======================================
# 📁 agents/DependencyAgent/app/agent.py
# =======================================
import os
import json
import datetime
import uuid
import asyncio
import logging
from collections import defaultdict # Was missing from previous if used by PlanAgent for adj list
from typing import Dict, Any, Optional, List 

# --- Observability Setup ---
SERVICE_NAME = "DependencyAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s'
)
tracer = None
try:
    # Assuming core.observability.tracing is in PYTHONPATH
    from core.observability.tracing import setup_tracing
    tracer = setup_tracing(SERVICE_NAME)
except ImportError:
    logging.getLogger(SERVICE_NAME).warning(
        "DependencyAgent: Tracing setup failed. Ensure core.observability.tracing is available and PYTHONPATH is correct."
    )
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
# Import from your core.build_graph module
from core.build_graph import detect_changed_tasks, get_project_dag 
from interfaces.types.events import NewCommitEvent, AffectedTasksIdentifiedEvent, FileChange

COMMIT_EVENT_CHANNEL_PATTERN = "events.project.*.new_commit" # Listen for new commits
AFFECTED_TASKS_EVENT_CHANNEL_TEMPLATE = "events.project.{project_id}.affected_tasks.identified"

class DependencyAgent:
    def __init__(self):
        logger.info("Initializing DependencyAgent...")
        self.event_bus = EventBus()
        if not self.event_bus.redis_client:
            logger.error("DependencyAgent critical: EventBus not connected.")
        logger.info("DependencyAgent Initialized.")

    @property
    def tracer(self): # For OpenTelemetry
        return tracer

    def _determine_affected_tasks(self, project_id: str, changed_files_paths: List[str]) -> List[str]:
        """
        Uses the core.build_graph logic to determine affected tasks.
        """
        current_span_context = None
        if self.tracer:
            current_span = trace.get_current_span() # Get current span from context
            current_span_context = trace.set_span_in_context(current_span)

        span_name = "dependency_agent._determine_affected_tasks"
        
        if not self.tracer: # Fallback if tracer couldn't initialize
            return self.__determine_affected_tasks_logic(project_id, changed_files_paths)

        with self.tracer.start_as_current_span(span_name, context=current_span_context) as span:
            span.set_attributes({
                "dependency_agent.project_id": project_id,
                "dependency_agent.num_input_changed_files": len(changed_files_paths)
            })
            results = self.__determine_affected_tasks_logic(project_id, changed_files_paths)
            span.set_attribute("dependency_agent.num_affected_tasks_determined", len(results))
            return results

    def __determine_affected_tasks_logic(self, project_id: str, changed_files_paths: List[str]) -> List[str]:
        if not changed_files_paths:
            logger.info(f"No changed files provided for project {project_id}, defaulting to full DAG.")
            affected = get_project_dag(project_id) # from core.build_graph
            logger.info(f"Defaulting to all tasks for project {project_id}: {affected}")
            return affected

        logger.info(f"Detecting affected tasks for project {project_id} based on {len(changed_files_paths)} changed file(s).")
        affected_tasks = detect_changed_tasks(project=project_id, changed_files=changed_files_paths) # from core.build_graph
        logger.info(f"Affected tasks for project {project_id}: {affected_tasks}")
        return affected_tasks

    async def handle_new_commit_event(self, event_data_str: str):
        # Extract trace context from event_data_str if it was propagated, e.g. from headers or message attributes
        # For now, we assume a new trace might start here or link to an existing one if context is found.
        # This example doesn't show context propagation from Redis message, but OTel libraries might have ways.
        span_name = "dependency_agent.handle_new_commit_event"
        
        if not self.tracer: # Fallback
            await self._handle_new_commit_event_logic_from_string(event_data_str)
            return

        with self.tracer.start_as_current_span(span_name) as span: # Starts a new trace or child span
            try:
                event: NewCommitEvent = json.loads(event_data_str)
                # Basic validation
                if not (event.get("event_type") == "NewCommitEvent" and 
                        "project_id" in event and "commit_sha" in event and "changed_files" in event):
                    logger.error(f"Malformed NewCommitEvent: {event_data_str[:200]}")
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "Malformed event data"))
                    return
                
                span.set_attributes({
                    "messaging.system": "redis", # OTel semantic convention
                    "messaging.operation": "process",
                    "messaging.message.id": event.get("event_id", str(uuid.uuid4())), # Assuming event_id in NewCommitEvent
                    "messaging.destination.name": COMMIT_EVENT_CHANNEL_PATTERN, # The channel pattern listened to
                    "dependency_agent.project_id": event["project_id"],
                    "dependency_agent.commit_sha": event["commit_sha"],
                    "dependency_agent.num_changed_files": len(event["changed_files"])
                })
                logger.info(f"DependencyAgent handling NewCommitEvent for project {event['project_id']}, commit {event['commit_sha']}")
                
                # Call the core logic, passing the parsed event
                await self._handle_new_commit_event_logic(event)

            except json.JSONDecodeError:
                logger.error(f"Could not decode JSON from event: {event_data_str[:200]}")
                span.set_status(trace.Status(trace.StatusCode.ERROR, "JSON decode error"))
            except Exception as e:
                logger.error(f"Error handling NewCommitEvent: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Core event handling failed"))
    
    async def _handle_new_commit_event_logic_from_string(self, event_data_str: str):
        """ Non-traced fallback for the main logic if tracer failed to initialize """
        try:
            event: NewCommitEvent = json.loads(event_data_str)
            if not (event.get("event_type") == "NewCommitEvent" and 
                    "project_id" in event and "commit_sha" in event and "changed_files" in event):
                logger.error(f"Malformed NewCommitEvent (no trace): {event_data_str[:200]}")
                return
            await self._handle_new_commit_event_logic(event)
        except json.JSONDecodeError:
            logger.error(f"Could not decode JSON from event (no trace): {event_data_str[:200]}")
        except Exception as e:
            logger.error(f"Error handling NewCommitEvent (no trace): {e}", exc_info=True)


    async def _handle_new_commit_event_logic(self, event: NewCommitEvent):
        project_id = event["project_id"]
        commit_sha = event["commit_sha"]
        
        changed_file_paths = [change['file_path'] for change in event["changed_files"]]
        affected_tasks = self._determine_affected_tasks(project_id, changed_file_paths)

        if affected_tasks:
            if self.event_bus.redis_client:
                # Create a new span for publishing the event, linking it to the current span
                span_publish_name = "dependency_agent.publish_affected_tasks_event"
                current_span_context = None
                if self.tracer:
                    current_span_for_publish = trace.get_current_span()
                    current_span_context = trace.set_span_in_context(current_span_for_publish)

                if not self.tracer: # Fallback
                     self._publish_affected_tasks_logic(event, project_id, commit_sha, affected_tasks)
                     return

                with self.tracer.start_as_current_span(span_publish_name, context=current_span_context) as pub_span:
                    self._publish_affected_tasks_logic(event, project_id, commit_sha, affected_tasks, pub_span)
            else:
                logger.error("Cannot publish AffectedTasksIdentifiedEvent: EventBus not connected.")
                if self.tracer: trace.get_current_span().set_attribute("publish_error", "event_bus_not_connected")
        elif not affected_tasks: # Explicitly check for empty list
            logger.info(f"No tasks determined to be affected for project {project_id}, commit {commit_sha}.")
            if self.tracer: trace.get_current_span().add_event("NoAffectedTasksFound")


    def _publish_affected_tasks_logic(self, event: NewCommitEvent, project_id: str, commit_sha: str, affected_tasks: List[str], pub_span: Optional[trace.Span] = None):
        """Helper for publishing, optionally within a tracing span"""
        affected_event = AffectedTasksIdentifiedEvent(
            event_type="AffectedTasksIdentifiedEvent",
            triggering_event_id=event.get("event_id", str(uuid.uuid4())), # Use event_id from NewCommitEvent
            project_id=project_id,
            commit_sha=commit_sha,
            affected_tasks=affected_tasks,
            timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        )
        channel = AFFECTED_TASKS_EVENT_CHANNEL_TEMPLATE.format(project_id=project_id)
        
        if pub_span:
            pub_span.set_attributes({
                "messaging.system": "redis",
                "messaging.destination.name": channel,
                "messaging.message.id": affected_event["triggering_event_id"], # Should be unique for the new event
                "dependency_agent.published_event_type": affected_event["event_type"],
                "dependency_agent.num_affected_tasks_published": len(affected_tasks)
            })

        self.event_bus.publish(channel, affected_event)
        logger.info(f"Published AffectedTasksIdentifiedEvent for project {project_id}, commit {commit_sha}. Tasks: {affected_tasks}")


    async def main_event_loop(self):
        if not self.event_bus.redis_client:
            logger.critical("DependencyAgent: Cannot start main event loop, EventBus not connected. Exiting after a delay.")
            await asyncio.sleep(60) # Avoid rapid restarts if containerized
            return

        pubsub = self.event_bus.subscribe_to_channel(COMMIT_EVENT_CHANNEL_PATTERN)
        if not pubsub:
            logger.critical(f"DependencyAgent: Failed to subscribe to {COMMIT_EVENT_CHANNEL_PATTERN}. Worker cannot start. Exiting after a delay.")
            await asyncio.sleep(60)
            return

        logger.info(f"DependencyAgent worker subscribed to {COMMIT_EVENT_CHANNEL_PATTERN}, listening for commit events...")
        try:
            while True:
                # Use asyncio.to_thread for the blocking pubsub.get_message call
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0) # Check for message every second
                if message and message["type"] == "pmessage": # pmessage for pattern subscriptions
                    logger.debug(f"Received raw message on {message['channel']}: {message_summary(message['data'])}")
                    # Create a new trace for each message received, or link if context is propagated
                    await self.handle_new_commit_event(message["data"]) 
                
                # Add a small sleep to prevent a very tight loop if get_message returns None immediately (e.g. due to timeout)
                # and to allow other asyncio tasks to run if any were created.
                await asyncio.sleep(0.01) 
        except KeyboardInterrupt:
            logger.info("DependencyAgent event loop interrupted by KeyboardInterrupt.")
        except redis.exceptions.ConnectionError as redis_err:
            logger.error(f"Redis connection error in main event loop: {redis_err}. Attempting to reconnect or exit.", exc_info=True)
            # Implement more robust reconnection logic or graceful shutdown here
            # For now, just log and it will eventually exit the loop.
            # Consider re-raising or exiting if reconnection fails after retries.
            raise
        except Exception as e:
            logger.error(f"Critical error in DependencyAgent event loop: {e}", exc_info=True)
            # Consider specific error handling or shutdown procedures
        finally:
            logger.info("DependencyAgent shutting down pubsub...")
            if pubsub:
                try: 
                    # These are blocking calls, should also be in a thread for clean async shutdown
                    await asyncio.to_thread(pubsub.punsubscribe, COMMIT_EVENT_CHANNEL_PATTERN)
                    await asyncio.to_thread(pubsub.close)
                except Exception as e_close:
                     logger.error(f"Error closing pubsub for DependencyAgent: {e_close}")
            logger.info("DependencyAgent shutdown complete.")

async def main(): # Wrapper for asyncio execution
    # Import trace here only if needed for main, typically setup_tracing handles global tracer
    # from opentelemetry import trace 
    agent = DependencyAgent()
    
    main_span_name = "dependency_agent.main_execution"
    if not agent.tracer: # Fallback if tracer couldn't initialize
        await agent.main_event_loop()
        return

    with agent.tracer.start_as_current_span(main_span_name) as main_span:
        try:
            await agent.main_event_loop()
            main_span.set_status(trace.StatusCode.OK)
        except Exception as e:
            logger.critical(f"DependencyAgent main execution failed: {e}", exc_info=True)
            main_span.record_exception(e)
            main_span.set_status(trace.Status(trace.StatusCode.ERROR, "Main event loop crashed"))
            raise # Re-raise to ensure process exits with error if desired

if __name__ == "__main__":
    # This structure ensures that setup_tracing is called once when the module is imported,
    # and then the main async logic runs.
    # from opentelemetry import trace # For setting status/attributes directly on trace if needed
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("DependencyAgent process stopped by user (KeyboardInterrupt in __main__).")
    except Exception as e: # Catch-all for any unhandled error during asyncio.run(main())
        # This might catch errors from the main_event_loop if it re-raised them
        logger.critical(f"DependencyAgent failed to run or unhandled error in __main__: {e}", exc_info=True)
        # Exit with an error code if the main execution fails critically
        # import sys
        # sys.exit(1)
