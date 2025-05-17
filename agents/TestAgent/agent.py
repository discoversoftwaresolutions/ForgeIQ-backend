# =====================================
# 📁 agents/TestAgent/agent.py (V0.2)
# =====================================
import os
import time
import json
import datetime
import uuid
import random # Still used for selecting projects in sim mode
import hashlib # For mock commit sha
import asyncio
import logging
import subprocess # To run actual test commands
from typing import Dict, Any, Optional, List

# --- Observability Setup ---
SERVICE_NAME = "TestAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s' 
) # OTel LoggingInstrumentor will add trace/span IDs
_tracer = None
_trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing
    _tracer = setup_tracing(SERVICE_NAME)
    _trace_api = otel_trace_api
except ImportError:
    logging.getLogger(SERVICE_NAME).warning("TestAgent: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from interfaces.types.events import TestFailedEvent, TestResult

# Configuration
CODE_BASE_MOUNT_PATH = os.getenv("CODE_BASE_MOUNT_PATH", "/codesrc")
DEFAULT_TEST_COMMAND_STR = os.getenv("DEFAULT_TEST_COMMAND", "pytest") # e.g., "pytest -v", "make test"

# Channel for publishing test failure events
TEST_FAILURE_EVENT_CHANNEL_TEMPLATE = "events.project.{project_id}.test.failures"
# Conceptual channel this agent might listen to for "run test" commands
# RUN_TESTS_COMMAND_CHANNEL_PATTERN = "commands.project.*.run_tests" 

# For simulation mode if actual command execution is not desired yet
SIMULATION_MODE = os.getenv("TEST_AGENT_SIMULATION_MODE", "false").lower() == "true"
MOCK_PROJECT_TEST_SUITES = { # Used only if SIMULATION_MODE is true
    "project_alpha": {"tests": ["test_A1", "test_A2"], "failure_rate": 0.3},
    "project_beta": {"tests": ["test_B1", "test_B2", "test_B3"], "failure_rate": 0.1}
}

class TestAgent:
    def __init__(self):
        logger.info(f"Initializing TestAgent (V0.2 - Command Execution, SimMode: {SIMULATION_MODE})...")
        self.event_bus = EventBus()
        if not self.event_bus.redis_client:
            logger.error("TestAgent critical: Could not connect to EventBus.")
        logger.info("TestAgent V0.2 Initialized.")

    @property
    def tracer_instance(self):
        return _tracer

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same _start_trace_span_if_available helper as in other agents)
        if _tracer and _trace_api:
            span = _tracer.start_span(f"test_agent.{operation_name}", context=parent_context)
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

    def _execute_test_command(self, project_id: str, commit_sha: str, test_command_str: str, project_code_path: str) -> Tuple[int, str, str]:
        """
        Executes the given test command in the specified project code path.
        Returns (exit_code, stdout, stderr).
        """
        span = self._start_trace_span_if_available("execute_test_command", 
                                                   project_id=project_id, commit_sha=commit_sha, 
                                                   command=test_command_str, path=project_code_path)
        logger.info(f"Executing test command '{test_command_str}' in '{project_code_path}' for project '{project_id}'")

        # Split command string into a list for subprocess if it's not already
        command_parts = test_command_str.split() if isinstance(test_command_str, str) else test_command_str

        process = None
        try:
            with span: #type: ignore
                # Important: Ensure the environment for subprocess has necessary PATH, Python env, etc.
                # For Docker, this means the base image and its setup are critical.
                process = subprocess.Popen(
                    command_parts,
                    cwd=project_code_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8'
                )
                stdout, stderr = process.communicate(timeout=3600) # 1 hour timeout for tests
                exit_code = process.returncode

                if _tracer: 
                    span.set_attribute("test_execution.exit_code", exit_code)
                    # Log limited stdout/stderr to traces to avoid large trace sizes
                    span.set_attribute("test_execution.stdout_preview", stdout[:1024])
                    span.set_attribute("test_execution.stderr_preview", stderr[:1024])

                logger.info(f"Test command for '{project_id}' finished with exit code {exit_code}.")
                logger.debug(f"Stdout for '{project_id}':\n{stdout}")
                if stderr:
                    logger.debug(f"Stderr for '{project_id}':\n{stderr}")
                return exit_code, stdout, stderr

        except subprocess.TimeoutExpired:
            logger.error(f"Test command timed out for '{project_id}': {test_command_str}")
            if process: process.kill(); process.communicate() # Ensure cleanup
            if _tracer and _trace_api and span : span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Test command timeout"))
            return -1, "", "Test command timed out." # Use -1 or other convention for timeout
        except FileNotFoundError:
            logger.error(f"Test command not found for '{project_id}': {command_parts[0]}")
            if _tracer and _trace_api and span : span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Test command not found"))
            return -2, "", f"Command not found: {command_parts[0]}"
        except Exception as e:
            logger.error(f"Error executing test command for '{project_id}': {e}", exc_info=True)
            if _tracer and _trace_api and span : span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Test execution exception"))
            return -3, "", str(e)


    def _parse_test_results_from_output(self, exit_code: int, stdout: str, stderr: str, project_id: str) -> List[TestResult]:
        """
        Parses test results from stdout/stderr.
        For V0.2, this is a simple parser: if exit_code != 0, assume one generic failure.
        Future: Integrate with JUnit XML, pytest JSON report, etc.
        """
        if exit_code == 0:
            return [TestResult(test_name="all_tests_suite", status="passed", message="All tests passed.", stack_trace=None)]
        else:
            # Basic failure reporting
            return [TestResult(
                test_name="test_suite_failure", # Generic name for overall failure
                status="failed",
                message=f"Test suite failed with exit code {exit_code}. Check logs.",
                # Combine stdout and stderr for a basic "stack_trace" or details
                stack_trace=(f"STDOUT:\n{stdout[:2000]}\n\nSTDERR:\n{stderr[:2000]}" if stdout or stderr else "No output captured.")[:4000] # Limit length
            )]

    async def process_test_run_command(self, 
                                     project_id: str, 
                                     commit_sha: str, 
                                     test_command_override: Optional[str] = None,
                                     trigger_id: Optional[str] = None):
        span_name = f"test_agent.process_test_run:{project_id}:{commit_sha}"
        parent_context = None # Extract if present in trigger event
        span = self._start_trace_span_if_available(span_name, parent_context=parent_context, project_id=project_id, commit_sha=commit_sha)

        try:
            with span: #type: ignore
                logger.info(f"Processing test run command for project '{project_id}', commit '{commit_sha}'")
                if _tracer: span.set_attribute("trigger.id", trigger_id or "N/A")

                if SIMULATION_MODE:
                    # Use the simulation logic from V0.1
                    test_results_list = self._simulate_test_run_v01(project_id, commit_sha, span)
                else:
                    project_code_path = os.path.join(CODE_BASE_MOUNT_PATH, project_id)
                    if not os.path.isdir(project_code_path):
                        logger.error(f"Project code path not found for testing: {project_code_path}")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Project code path not found"))
                        # Optionally publish a TestFailedEvent indicating setup error
                        self._publish_test_failure(project_id, commit_sha, [TestResult(test_name="setup_error", status="failed", message=f"Project code path not found: {project_code_path}", stack_trace=None)], trigger_id)
                        return

                    command_to_run = test_command_override or DEFAULT_TEST_COMMAND_STR
                    if _tracer: span.set_attribute("test.command", command_to_run)

                    exit_code, stdout, stderr = await asyncio.to_thread(
                        self._execute_test_command, project_id, commit_sha, command_to_run, project_code_path
                    )
                    test_results_list = self._parse_test_results_from_output(exit_code, stdout, stderr, project_id)

                failed_tests = [result for result in test_results_list if result["status"] == "failed"]

                if failed_tests:
                    self._publish_test_failure(project_id, commit_sha, failed_tests, trigger_id, span)
                else:
                    logger.info(f"All tests reported as passed for project '{project_id}', commit '{commit_sha}'.")
                    if _tracer: span.add_event("AllTestsPassedOrNoFailuresReported")

                if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
        except Exception as e:
            logger.error(f"Unexpected error in process_test_run_command for {project_id}: {e}", exc_info=True)
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Test processing error"))


    def _publish_test_failure(self, project_id:str, commit_sha:str, failed_tests: List[TestResult], trigger_id: Optional[str]=None, parent_span: Optional[Any]=None):
        if not self.event_bus.redis_client:
            logger.error("Cannot publish TestFailedEvent: EventBus not connected.")
            if parent_span and _tracer and _trace_api: parent_span.set_attribute("event_publish_error", "event_bus_not_connected")
            return

        span = self._start_trace_span_if_available("publish_test_failure_event", parent_context=otel_trace_api.set_span_in_context(parent_span) if parent_span and _trace_api else None)
        try:
            with span: #type: ignore
                event_id = str(uuid.uuid4()) # ID for this specific failure event
                if _tracer: span.set_attribute("event.id", event_id)

                failure_event_data = TestFailedEvent(
                    event_type="TestFailedEvent",
                    # id=event_id, # Add 'id' to TestFailedEvent TypedDict if desired
                    project_id=project_id,
                    commit_sha=commit_sha,
                    failed_tests=failed_tests,
                    full_log_path=f"/logs/{project_id}/{commit_sha}/test_run_{event_id}.log", # Example path
                    timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                )
                channel = TEST_FAILURE_EVENT_CHANNEL_TEMPLATE.format(project_id=project_id)
                self.event_bus.publish(channel=channel, event_data=failure_event_data) # type: ignore
                if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
        except Exception as e:
            logger.error(f"Error publishing TestFailedEvent: {e}", exc_info=True)
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Publish failed"))


    def _simulate_test_run_v01(self, project_id: str, commit_sha: str, parent_span: Optional[Any] = None) -> List[TestResult]:
        """Original V0.1 simulation logic, now a helper."""
        # ... (This is the simulate_test_run logic from response #61 TestAgent V0.1,
        #      ensure it uses logger, and can be called from the new traced context)
        # For brevity, I'll assume it's the same as before.
        # It should return List[TestResult]
        logger.info(f"[SIMULATION] Running tests for project {project_id} at commit {commit_sha}...")
        if parent_span: parent_span.set_attribute("simulation.active", True)
        time.sleep(random.randint(2,5)) 
        project_config = MOCK_PROJECT_TEST_SUITES.get(project_id, {"tests": [f"sim_test_{i}" for i in range(3)], "failure_rate": 0.2})
        results: List[TestResult] = []
        run_has_failure = random.random() < project_config["failure_rate"]
        failed_test_indices = []
        if run_has_failure:
            num_failures = random.randint(1, max(1, len(project_config["tests"]) // 2))
            failed_test_indices = random.sample(range(len(project_config["tests"])), num_failures)

        for i, test_name in enumerate(project_config["tests"]):
            status = "passed"; message = "Simulated pass."; stack_trace = None
            if i in failed_test_indices:
                status = "failed"; message = f"Simulated failure in {test_name}"
                stack_trace = f"Traceback (simulated):\n  Simulated error in {test_name} at line {random.randint(10,50)}"
            results.append(TestResult(test_name=test_name, status=status, message=message, stack_trace=stack_trace))
        return results


    async def main_event_loop(self):
        """
        Main worker loop. Could listen for 'RunTestsCommand' events.
        For V0.2, it will periodically trigger test runs for known projects if in actual execution mode.
        """
        if SIMULATION_MODE:
            logger.info("TestAgent worker started in SIMULATION_MODE. Will simulate periodic test runs.")
        else:
            logger.info("TestAgent worker started in EXECUTION_MODE. Will simulate periodic test run triggers (actual commands).")

        projects_to_test = list(MOCK_PROJECT_TEST_SUITES.keys()) # In real scenario, this comes from config or AgentRegistry

        while True:
            try:
                # This loop simulates being triggered, e.g., by a scheduler or another event.
                if projects_to_test:
                    project_to_run = random.choice(projects_to_test)
                    mock_commit_sha = hashlib.sha1(os.urandom(16)).hexdigest()[:10]

                    logger.info(f"Simulating trigger for test run: Project '{project_to_run}', Commit '{mock_commit_sha}'")
                    # In a real system, test_command_override might come from the trigger event or project config
                    await self.process_test_run_command(project_to_run, mock_commit_sha, test_command_override=None) 

                sleep_duration = random.randint(60, 180) # Simulate varied workload or check interval
                logger.debug(f"TestAgent sleeping for {sleep_duration} seconds before next simulated trigger.")
                await asyncio.sleep(sleep_duration)
            except KeyboardInterrupt:
                logger.info("TestAgent worker shutting down...")
                break
            except Exception as e:
                logger.error(f"Critical error in TestAgent worker loop: {e}", exc_info=True)
                await asyncio.sleep(60) # Wait a bit before retrying after an unexpected error

async def main_async_runner():
    agent = TestAgent()
    await agent.main_event_loop()

if __name__ == "__main__":
    # hashlib is needed for mock_commit_sha in the example simulation loop
    try:
        asyncio.run(main_async_runner())
    except KeyboardInterrupt:
        logger.info("TestAgent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"TestAgent failed to start or unhandled error in __main__: {e}", exc_info=True)
