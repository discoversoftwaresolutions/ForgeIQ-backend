# agents/TestAgent//agent.py
import os
import time
import json
import datetime
import uuid
import random
import logging

# Assuming your project structure allows these imports
# Ensure __init__.py files are in 'core' and 'interfaces/types' and their subdirectories
from core.event_bus.redis_bus import EventBus 
from interfaces.types.events import TestFailedEvent, TestResult

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Mock test configurations (in a real system, this would come from project configs)
MOCK_PROJECT_TEST_SUITES = {
    "project_alpha": {
        "tests": ["test_login_feature", "test_data_processing", "test_api_endpoint_A"],
        "failure_rate": 0.3 # 30% chance any given test run will have a failure
    },
    "project_beta": {
        "tests": ["test_core_module_init", "test_user_permissions", "test_report_generation"],
        "failure_rate": 0.1
    }
}

class TestAgent:
    def __init__(self):
        logger.info("Initializing TestAgent...")
        self.event_bus = EventBus()
        if not self.event_bus.redis_client:
            logger.error("TestAgent: Could not connect to EventBus. Publishing will fail.")
        logger.info("TestAgent Initialized.")

    def simulate_test_run(self, project_id: str, commit_sha: str) -> List[TestResult]:
        """Simulates running tests for a project and returns results."""
        results: List[TestResult] = []
        project_config = MOCK_PROJECT_TEST_SUITES.get(project_id)

        if not project_config:
            logger.warning(f"No mock test configuration found for project_id: {project_id}")
            return results

        has_failure_in_run = random.random() < project_config["failure_rate"]
        failed_test_chosen = False

        for test_name in project_config["tests"]:
            status = "passed"
            message: Optional[str] = "Test passed successfully."
            stack_trace: Optional[str] = None

            if has_failure_in_run and not failed_test_chosen and random.random() < 0.5: # Ensure at least one failure if run is failed
                status = "failed"
                message = f"AssertionError: Something went wrong in {test_name}"
                stack_trace = (
                    f"Traceback (most recent call last):\n"
                    f"  File \"/app/tests/{project_id}/{test_name.replace('test_', '')}.py\", line {random.randint(10,100)}, in {test_name}\n"
                    f"    assert important_value == expected_value, \"{message}\""
                )
                failed_test_chosen = True

            results.append(TestResult(
                test_name=test_name,
                status=status,
                message=message,
                stack_trace=stack_trace
            ))

        # If the run was marked for failure but no test was chosen, fail the last one.
        if has_failure_in_run and not failed_test_chosen and results:
            results[-1]["status"] = "failed"
            results[-1]["message"] = f"AssertionError: A critical assertion failed at the end of {results[-1]['test_name']}"
            results[-1]["stack_trace"] = f"Traceback: ... some critical failure in {results[-1]['test_name']}"


        logger.info(f"Simulated test run for {project_id} ({commit_sha}): {len(results)} tests, {len([r for r in results if r['status'] == 'failed'])} failed.")
        return results

    def process_test_run_request(self, project_id: str, commit_sha: str):
        """
        Simulates a full test run cycle: run tests, and if failures, publish an event.
        In a real system, this might be triggered by an event (e.g., NewCommitEvent).
        """
        logger.info(f"Processing test run request for project '{project_id}', commit '{commit_sha}'")
        test_results = self.simulate_test_run(project_id, commit_sha)

        failed_tests = [result for result in test_results if result["status"] == "failed"]

        if failed_tests:
            if self.event_bus.redis_client: # Ensure client is connected
                failure_event_data = TestFailedEvent(
                    event_type="TestFailedEvent",
                    project_id=project_id,
                    commit_sha=commit_sha,
                    failed_tests=failed_tests,
                    full_log_path=f"/logs/{project_id}/{commit_sha}/test_run_{uuid.uuid4()}.log", # Example path
                    timestamp=datetime.datetime.utcnow().isoformat()
                )
                # Define a specific channel for test failures, perhaps per project or global
                channel = f"test_events.{project_id}.failures" 
                self.event_bus.publish(channel=channel, event_data=failure_event_data)
            else:
                logger.error("Cannot publish TestFailedEvent: EventBus not connected.")
        else:
            logger.info(f"All tests passed for project '{project_id}', commit '{commit_sha}'. No failure event published.")

    def run_worker(self):
        """
        Main worker loop. In a real system, this would listen to an event bus for
        "RunTestsCommand" or similar. For now, it just simulates some periodic activity.
        """
        logger.info("TestAgent worker started. Simulating periodic test runs.")
        projects_to_test = list(MOCK_PROJECT_TEST_SUITES.keys())
        while True:
            try:
                # Simulate being triggered to run tests for a random project
                if projects_to_test:
                    project_to_run = random.choice(projects_to_test)
                    # Generate a mock commit SHA
                    mock_commit_sha = hashlib.sha1(os.urandom(16)).hexdigest()[:10]
                    self.process_test_run_request(project_to_run, mock_commit_sha)

                # In a real scenario, this loop would be driven by messages from a queue
                # or specific triggers, not a blind time.sleep.
                sleep_duration = random.randint(30, 90) # Simulate varied workload
                logger.debug(f"TestAgent sleeping for {sleep_duration} seconds before next simulated run.")
                time.sleep(sleep_duration)
            except KeyboardInterrupt:
                logger.info("TestAgent worker shutting down...")
                break
            except Exception as e:
                logger.error(f"Error in TestAgent worker loop: {e}", exc_info=True)
                time.sleep(60) # Wait a bit before retrying after an unexpected error

if __name__ == "__main__":
    import hashlib # Moved import here as it's only used in main's example logic now
    agent = TestAgent()
    agent.run_worker()
