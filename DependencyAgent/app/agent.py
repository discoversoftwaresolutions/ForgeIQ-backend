SERVICE_NAME = "DependencyAgent"
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
            "DependencyAgent: Tracing setup failed."
        )
    logger = logging.getLogger(__name__)
    # --- End Observability Setup ---

    from core.event_bus.redis_bus import EventBus, message_summary
    # Import from your core.build_graph module
    from core.build_graph import detect_changed_tasks, get_project_dag # Assuming these functions exist
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
        def tracer(self):
            return tracer

        def _determine_affected_tasks(self, project_id: str, changed_files_paths: List[str]) -> List[str]:
            """
            Uses the core.build_graph logic to determine affected tasks.
            """
            # The detect_changed_tasks function from your core.build_graph takes project and List[str]
            # It uses FILE_TASK_MAP and PROJECT_GRAPH
            # This is a direct integration with the Python code you provided for core.build_graph.
            if not changed_files_paths:
                logger.info(f"No changed files provided for project {project_id}, defaulting to full DAG.")
                # If no files changed (e.g. manual trigger), run all tasks in project DAG
                affected = get_project_dag(project_id)
                logger.info(f"Defaulting to all tasks for project {project_id}: {affected}")
                return affected

            logger.info(f"Detecting affected tasks for project {project_id} based on {len(changed_files_paths)} changed file(s).")
            affected_tasks = detect_changed_tasks(project=project_id, changed_files=changed_files_paths)
            logger.info(f"Affected tasks for project {project_id}: {affected_tasks}")
            return affected_tasks

        async def handle_new_commit_event(self, event_data_str: str):
            span_name = "dependency_agent.handle_new_commit_event"
            if not self.tracer: # Fallback
                await self._handle_new_commit_event_logic(event_data_str)
                return

            with self.tracer.start_as_current_span(span_name) as span:
                try:
                    event: NewCommitEvent = json.loads(event_data_str)
                    if not (event.get("event_type") == "NewCommitEvent" and 
                            "project_id" in event and "commit_sha" in event and "changed_files" in event):
                        logger.error(f"Malformed NewCommitEvent: {event_data_str[:200]}")
                        span.set_status(trace.Status(trace.StatusCode.ERROR, "Malformed event"))
                        return
                    
                    span.set_attributes({
                        "messaging.system": "redis",
                        "dependency_agent.event_id": event.get("event_id"),
                        "dependency_agent.project_id": event["project_id"],
                        "dependency_agent.commit_sha": event["commit_sha"],
                        "dependency_agent.num_changed_files": len(event["changed_files"])
                    })
                    logger.info(f"DependencyAgent handling NewCommitEvent for project {event['project_id']}, commit {event['commit_sha']}")
                    await self._handle_new_commit_event_logic(event)

                except json.JSONDecodeError:
                    logger.error(f"Could not decode JSON from event: {event_data_str[:200]}")
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "JSON decode error"))
                except Exception as e:
                    logger.error(f"Error handling NewCommitEvent: {e}", exc_info=True)
                    span.record_exception(e)
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "Event handling failed"))
        
        async def _handle_new_commit_event_logic(self, event: NewCommitEvent):
            project_id = event["project_id"]
            commit_sha = event["commit_sha"]
            
            # Extract just the paths from FileChange objects
            changed_file_paths = [change['file_path'] for change in event["changed_files"]]

            affected_tasks = self._determine_affected_tasks(project_id, changed_file_paths)

            if affected_tasks and self.event_bus.redis_client:
                affected_event = AffectedTasksIdentifiedEvent(
                    event_type="AffectedTasksIdentifiedEvent",
                    triggering_event_id=event.get("event_id", str(uuid.uuid4())),
                    project_id=project_id,
                    commit_sha=commit_sha,
                    affected_tasks=affected_tasks,
                    timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                )
                channel = AFFECTED_TASKS_EVENT_CHANNEL_TEMPLATE.format(project_id=project_id)
                self.event_bus.publish(channel, affected_event)
                logger.info(f"Published AffectedTasksIdentifiedEvent for project {project_id}, commit {commit_sha}. Tasks: {affected_tasks}")
                if self.tracer: trace.get_current_span().add_event("Published AffectedTasksIdentifiedEvent", {"num_affected_tasks": len(affected_tasks)})
            elif not affected_tasks:
                logger.info(f"No tasks determined to be affected for project {project_id}, commit {commit_sha}.")
                if self.tracer: trace.get_current_span().add_event("NoAffectedTasksFound")
            else: # Event bus not connected
                logger.error("Cannot publish AffectedTasksIdentifiedEvent: EventBus not connected.")


        async def main_event_loop(self):
            if not self.event_bus.redis_client:
                logger.critical("DependencyAgent: Cannot start, EventBus not connected.")
                return

            pubsub = self.event_bus.subscribe_to_channel(COMMIT_EVENT_CHANNEL_PATTERN)
            if not pubsub:
                logger.critical(f"DependencyAgent: Failed to subscribe to {COMMIT_EVENT_CHANNEL_PATTERN}. Worker cannot start.")
                return

            logger.info(f"DependencyAgent worker subscribed to {COMMIT_EVENT_CHANNEL_PATTERN}, listening for commit events...")
            try:
                while True:
                    message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                    if message and message["type"] == "pmessage": # pmessage for pattern subscriptions
                        await self.handle_new_commit_event(message["data"])
                    await asyncio.sleep(0.01)
            except KeyboardInterrupt:
                logger.info("DependencyAgent event loop interrupted.")
            except Exception as e:
                logger.error(f"Critical error in DependencyAgent event loop: {e}", exc_info=True)
            finally:
                logger.info("DependencyAgent shutting down pubsub...")
                if pubsub:
                    try: await asyncio.to_thread(pubsub.punsubscribe, COMMIT_EVENT_CHANNEL_PATTERN)
                    except: pass
                    try: await asyncio.to_thread(pubsub.close)
                    except: pass
                logger.info("DependencyAgent shutdown complete.")

async def main():
    agent = DependencyAgent()
    await agent.main_event_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("DependencyAgent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"DependencyAgent failed to start or unhandled error in main: {e}", exc_info=True)
    ```
