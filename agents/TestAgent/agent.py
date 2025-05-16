# agents/TestAgent/agent.py
import os
import time
import json
import datetime
import uuid
import random
import logging

# --- Observability Setup (done once per process) ---
SERVICE_NAME = "TestAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# Basic logging config (OTel LoggingInstrumentor will enhance it)
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
        "Tracing setup failed. Ensure core.observability.tracing is available."
    )
logger = logging.getLogger(__name__) # Get logger after basicConfig and OTel setup
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus
from interfaces.types.events import TestFailedEvent, TestResult

MOCK_PROJECT_TEST_SUITES = {
    "project_alpha": {
        "tests": ["test_login_feature", "test_data_processing", "test_api_endpoint_A"],
        "failure_rate": 0.4 # Increased for more frequent failure events
    },
    "project_beta": {
        "tests": ["test_core_module_init", "test_user_permissions", "test_report_generation"],
        "failure_rate": 0.2
    }
}
# Channel for publishing test failure events
TEST_FAILURE_EVENT_CHANNEL_TEMPLATE = "events.project.{project_id}.test.failures"
# Channel this agent might listen to for "run test" commands (conceptual)
RUN_TESTS_COMMAND_CHANNEL = "commands.test_agent.run_tests"


class TestAgent:
    def __init__(self):
        logger.info("Initializing TestAgent...")
        self.event_bus = EventBus()
        if not self.event_bus.redis_client:
            logger.error("TestAgent critical: Could not connect to EventBus. Publishing will fail.")
        logger.info("TestAgent Initialized and connected to EventBus.")

    def _generate_mock_stack_trace(self, project_id: str, test_name: str, message: str) -> str:
        # Generates a more varied mock stack trace
        depth = random.randint(3, 7)
        trace_lines = ["Traceback (most recent call last):"]
        for i in range(depth):
            file_num = random.randint(1,3)
            line_num = random.randint(10, 150)
            func_name = random.choice(["process_item", "calculate_value", "handle_request", "internal_check"])
            trace_lines.append(
                f"  File \"/app/apps/{project_id}/module{chr(65+file_num)}.py\", line {line_num}, in {func_name}"
            )
        trace_lines.append(f"AssertionError: {message}")
        return "\n".join(trace_lines)

    def simulate_test_run(self, project_id: str, commit_sha: str) -> List[TestResult]:
        """Simulates running tests for a project and returns results."""
        current_span = trace.get_current_span()
        current_span.set_attributes({
            "test_agent.project_id": project_id,
            "test_agent.commit_sha": commit_sha,
            "test_agent.simulation_mode": True
        })

        results: List[TestResult] = []
        project_config = MOCK_PROJECT_TEST_SUITES.get(project_id)

        if not project_config:
            logger.warning(f"No mock test configuration found for project_id: {project_id}")
            current_span.set_attribute("test_agent.error", "no_project_config")
            return results

        run_has_failure = random.random() < project_config["failure_rate"]
        num_tests = len(project_config["tests"])
        failed_test_indices = []
        if run_has_failure:
            num_failures = random.randint(1, max(1, num_tests // 2)) # Fail 1 to half of tests
            failed_test_indices = random.sample(range(num_tests), num_failures)

        for i, test_name in enumerate(project_config["tests"]):
            status = "passed"
            message: Optional[str] = "Test passed successfully."
            stack_trace: Optional[str] = None

            if i in failed_test_indices:
                status = "failed"
                message = f"Assertion failed in {test_name}: expected {random.randint(0,100)} but got {random.randint(0,100)}"
                stack_trace = self._generate_mock_stack_trace(project_id, test_name, message)

            results.append(TestResult(
                test_name=test_name,
                status=status,
                message=message,
                stack_trace=stack_trace
            ))

        num_failed = len(failed_test_indices)
        logger.info(f"Simulated test run for {project_id} ({commit_sha}): {num_tests} tests, {num_failed} failed.")
        current_span.set_attributes({
            "test_agent.num_tests_run": num_tests,
            "test_agent.num_tests_failed": num_failed
        })
        return results

    def process_test_run_command(self, project_id: str, commit_sha: str, trigger_id: Optional[str] = None):
        """
        Processes a request to run tests, then publishes results.
        `trigger_id` can be used for correlation if this was triggered by an event.
        """
        span_name = f"test_run:{project_id}:{commit_sha}"
        if tracer:
            with tracer.start_as_current_span(span_name) as span:
                span.set_attribute("messaging.system", "internal_command")
                if trigger_id:
                    span.set_attribute("messaging.conversation_id", trigger_id)

                logger.info(f"Processing test run command for project '{project_id}', commit '{commit_sha}'")
                test_results = self.simulate_test_run(project_id, commit_sha)
                failed_tests = [result for result in test_results if result["status"] == "failed"]

                if failed_tests:
                    if self.event_bus.redis_client:
                        event_id = str(uuid.uuid4())
                        failure_event_data = TestFailedEvent(
                            event_type="TestFailedEvent",
                            # id=event_id, # If your TypedDict definition for TestFailedEvent includes 'id'
                            project_id=project_id,
                            commit_sha=commit_sha,
                            failed_tests=failed_tests,
                            full_log_path=f"/logs/{project_id}/{commit_sha}/test_run_{event_id}.log",
                            timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                        )
                        channel = TEST_FAILURE_EVENT_CHANNEL_TEMPLATE.format(project_id=project_id)
                        self.event_bus.publish(channel=channel, event_data=failure_event_data)
                        span.add_event("Published TestFailedEvent", {"event_id": event_id, "channel": channel, "num_failed": len(failed_tests)})
                    else:
                        logger.error("Cannot publish TestFailedEvent: EventBus not connected.")
                        span.set_attribute("event_publish_error", "event_bus_not_connected")
                else:
                    logger.info(f"All tests passed for project '{project_id}', commit '{commit_sha}'.")
                    span.add_event("AllTestsPassed")
        else: # No tracer
            logger.info(f"Processing test run command for project '{project_id}', commit '{commit_sha}' (tracing not initialized)")
            # Duplicate logic if no tracer, or structure to avoid duplication
            test_results = self.simulate_test_run(project_id, commit_sha) # ... and so on


    def listen_for_commands(self):
        """
        Main worker loop. Listens for commands on the event bus.
        For now, it just simulates periodic triggers for demonstration.
        """
        logger.info("TestAgent worker started. Waiting for tasks or simulating periodic runs.")

        # Conceptual: How it might listen to actual commands
        # command_pubsub = self.event_bus.subscribe_to_channel(RUN_TESTS_COMMAND_CHANNEL)
        # if command_pubsub:
        #     for message in command_pubsub.listen():
        #         if message["type"] == "message":
        #             try:
        #                 command_data = json.loads(message["data"])
        #                 if command_data.get("command_type") == "RunProjectTests":
        #                     project_id = command_data.get("project_id")
        #                     commit_sha = command_data.get("commit_sha")
        #                     trigger_id = command_data.get("id")
        #                     if project_id and commit_sha:
        #                         self.process_test_run_command(project_id, commit_sha, trigger_id)
        #             except Exception as e:
        #                 logger.error(f"Error processing command: {e}", exc_info=True)
        # else:
        #     logger.error("Could not subscribe to command channel. TestAgent will not receive commands.")

        # Fallback to simulation if not listening or no commands
        projects_to_test = list(MOCK_PROJECT_TEST_SUITES.keys())
        while True:
            try:
                if projects_to_test:
                    project_to_run = random.choice(projects_to_test)
                    mock_commit_sha = hashlib.sha1(os.urandom(16)).hexdigest()[:10]
                    logger.info(f"Simulating trigger for test run: Project {project_to_run}, Commit {mock_commit_sha}")
                    self.process_test_run_command(project_to_run, mock_commit_sha)

                sleep_duration = random.randint(45, 120) 
                logger.debug(f"TestAgent sleeping for {sleep_duration} seconds.")
                time.sleep(sleep_duration)
            except KeyboardInterrupt:
                logger.info("TestAgent worker shutting down...")
                break
            except Exception as e:
                logger.error(f"Critical error in TestAgent worker loop: {e}", exc_info=True)
                time.sleep(60) # Avoid rapid crash loop

if __name__ == "__main__":
    import hashlib # For mock_commit_sha generation in example
    agent = TestAgent()
    agent.listen_for_commands()
