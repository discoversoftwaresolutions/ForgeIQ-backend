```python
# =======================================
# 📁 agents/SecurityAgent/agent.py
# =======================================
import os
import json
import datetime
import uuid
import asyncio
import logging
import subprocess
import time
from typing import Dict, Any, Optional, List

# --- Observability Setup ---
SERVICE_NAME = "SecurityAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s'
)
tracer = None
try:
    from opentelemetry import trace
    from core.observability.tracing import setup_tracing
    tracer = setup_tracing(SERVICE_NAME)
except ImportError:
    logging.getLogger(SERVICE_NAME).warning(
        "SecurityAgent: Tracing setup failed."
    )
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from interfaces.types.events import NewCommitEvent, NewArtifactEvent, SecurityFinding, SecurityScanResultEvent

# Path within the container where project source code is mounted/available for scanning
CODE_BASE_MOUNT_PATH = os.getenv("CODE_BASE_MOUNT_PATH", "/codesrc") 
# Path for dependency files if not at project root (relative to project root being scanned)
PYTHON_REQ_FILE_PATH = os.getenv("PYTHON_REQ_FILE_PATH", "requirements.txt") 

# Event channels this agent listens to
NEW_COMMIT_EVENT_CHANNEL_PATTERN = "events.project.*.new_commit"
NEW_ARTIFACT_EVENT_CHANNEL_PATTERN = "events.project.*.new_artifact"
# Event channel this agent publishes to
SECURITY_SCAN_RESULT_CHANNEL_TEMPLATE = "events.project.{project_id}.security.scan_result"


class SecurityAgent:
    def __init__(self):
        logger.info("Initializing SecurityAgent...")
        self.event_bus = EventBus()
        if not self.event_bus.redis_client:
            logger.error("SecurityAgent critical: EventBus not connected.")
        logger.info("SecurityAgent Initialized.")

    @property
    def tracer_instance(self):
        return tracer

    def _run_scan_tool(self, command: List[str], cwd: Optional[str] = None) -> Dict[str, Any]:
        """Helper to run a security tool command and capture output."""
        logger.info(f"Running security scan command: {' '.join(command)} in {cwd or 'default CWD'}")
        start_time = time.time()
        try:
            process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate(timeout=300) # 5 min timeout
            returncode = process.returncode
            duration = time.time() - start_time
            
            if returncode != 0 and stderr: # Many tools output findings to stdout but errors to stderr
                logger.warning(f"Security tool {' '.join(command)} exited with {returncode}. Stderr: {stderr.strip()}")
            
            # Tools like bandit exit with non-zero if findings. Safety also.
            # We consider the scan itself successful if it ran, findings are separate.
            return {
                "status": "COMPLETED", 
                "stdout": stdout.strip(), 
                "stderr": stderr.strip(), 
                "exit_code": returncode,
                "duration": duration
            }
        except subprocess.TimeoutExpired:
            logger.error(f"Security tool {' '.join(command)} timed out.")
            return {"status": "TIMEOUT", "stdout": "", "stderr": "Scan timed out.", "duration": time.time() - start_time}
        except FileNotFoundError:
            logger.error(f"Security tool command not found: {command[0]}")
            return {"status": "CMD_NOT_FOUND", "stdout": "", "stderr": f"Command {command[0]} not found.", "duration": time.time() - start_time}
        except Exception as e:
            logger.error(f"Error running security tool {' '.join(command)}: {e}", exc_info=True)
            return {"status": "ERROR", "stdout": "", "stderr": str(e), "duration": time.time() - start_time}

    def _parse_bandit_results(self, json_output: str) -> List[SecurityFinding]:
        findings: List[SecurityFinding] = []
        try:
            data = json.loads(json_output)
            for result in data.get("results", []):
                findings.append(SecurityFinding(
                    finding_id=result.get("test_id", str(uuid.uuid4())),
                    rule_id=result.get("test_id"),
                    severity=result.get("issue_severity", "MEDIUM").upper(),
                    description=result.get("issue_text", "N/A"),
                    file_path=result.get("filename"),
                    line_number=result.get("line_number"),
                    code_snippet=result.get("code", "")[:500], # Truncate
                    remediation=f"Confidence: {result.get('issue_confidence')}. More info: {result.get('more_info')}",
                    tool_name="bandit"
                ))
        except json.JSONDecodeError:
            logger.error("Failed to parse Bandit JSON output.")
        return findings
    
    def _parse_safety_results(self, json_output: str) -> List[SecurityFinding]:
        findings: List[SecurityFinding] = []
        try:
            # Safety output is a list of lists/tuples: [vuln_id, package, version, description, cve_id_or_more_info_url]
            data = json.loads(json_output) 
            for vuln in data:
                if len(vuln) >= 4: # Basic check for expected structure
                    findings.append(SecurityFinding(
                        finding_id=vuln[0] or str(uuid.uuid4()), # Vuln ID
                        rule_id=vuln[0] or None, # CVE or other ID
                        severity="HIGH", # Default for dependency issues, can be refined
                        description=f"Vulnerability in {vuln[1]} version {vuln[2]}: {vuln[3]}",
                        file_path=PYTHON_REQ_FILE_PATH, # Points to the requirements file
                        line_number=None, # Dependency scans are not usually line specific
                        code_snippet=f"{vuln[1]}=={vuln[2]}",
                        remediation=f"More info: {vuln[4] if len(vuln) > 4 else 'N/A'}. Update package {vuln[1]}.",
                        tool_name="safety"
                    ))
        except json.JSONDecodeError:
            logger.error("Failed to parse Safety JSON output.")
        return findings

    async def run_sast_scan(self, project_id: str, code_path: str) -> List[SecurityFinding]:
        logger.info(f"Running SAST scan (Bandit) on {code_path} for project {project_id}")
        # Command: bandit -r /path/to/code -f json -o output.json
        # We capture stdout instead of writing to file for this example.
        # Ensure 'bandit' is installed in the Docker image (via requirements.txt)
        command = ["bandit", "-r", ".", "-f", "json", "-c", "/app/.bandit.yml"] # Assumes a .bandit.yml at root for config
        # CWD should be the specific code path to scan relative to /app
        # If CODE_BASE_MOUNT_PATH is /codesrc, and code_path is project_alpha, then cwd is /codesrc/project_alpha
        scan_result = self._run_scan_tool(command, cwd=code_path)

        if scan_result["status"] == "COMPLETED":
            return self._parse_bandit_results(scan_result["stdout"])
        else:
            logger.error(f"Bandit SAST scan failed or errored. Status: {scan_result['status']}. Stderr: {scan_result['stderr']}")
            return [SecurityFinding(finding_id=str(uuid.uuid4()), tool_name="bandit", severity="CRITICAL", description=f"SAST scan execution failed: {scan_result['status']} - {scan_result['stderr']}")]


    async def run_dependency_scan_python(self, project_id: str, project_code_path: str) -> List[SecurityFinding]:
        requirements_file = os.path.join(project_code_path, PYTHON_REQ_FILE_PATH)
        # If using a root requirements.txt, the project_code_path might just be CODE_BASE_MOUNT_PATH
        # and requirements_file could be /app/requirements.txt
        # For this example, assume requirements.txt is relative to the project being scanned
        # But your setup is root requirements.txt, so path should be /app/requirements.txt
        actual_req_file = "/app/requirements.txt" # Based on your root requirements.txt setup

        if not os.path.exists(actual_req_file):
            logger.warning(f"Python requirements file not found at {actual_req_file} for project {project_id}. Skipping dependency scan.")
            return []
        
        logger.info(f"Running Python dependency scan (Safety) on {actual_req_file} for project {project_id}")
        # Command: safety check -r requirements.txt --json
        # Ensure 'safety' is installed (via requirements.txt)
        command = ["safety", "check", "-r", actual_req_file, "--json"]
        scan_result = self._run_scan_tool(command) # Runs from /app context

        if scan_result["status"] == "COMPLETED":
            # Safety exits non-zero if vulns found, stdout has JSON.
            return self._parse_safety_results(scan_result["stdout"])
        else:
            logger.error(f"Safety dependency scan failed or errored. Status: {scan_result['status']}. Stderr: {scan_result['stderr']}")
            return [SecurityFinding(finding_id=str(uuid.uuid4()), tool_name="safety", severity="CRITICAL", description=f"Dependency scan execution failed: {scan_result['status']} - {scan_result['stderr']}")]


    async def handle_new_commit(self, event: NewCommitEvent):
        project_id = event["project_id"]
        commit_sha = event["commit_sha"]
        event_id = event.get("event_id", str(uuid.uuid4()))
        
        logger.info(f"SecurityAgent handling NewCommitEvent for project {project_id}, commit {commit_sha}")
        
        # Determine path to source code for this project inside the container
        # This assumes a convention like /codesrc/<project_id>/
        project_code_path = os.path.join(CODE_BASE_MOUNT_PATH, project_id) 
        if not os.path.isdir(project_code_path):
            logger.error(f"Source code path not found for project {project_id}: {project_code_path}")
            # Publish a scan failure event
            self._publish_scan_results(event_id, project_id, commit_sha, None, "SAST_SETUP_ERROR", "bandit", "FAILED_TO_SCAN", [], "Source code path not found")
            return

        all_findings: List[SecurityFinding] = []
        sast_findings: List[SecurityFinding] = []
        dep_scan_findings: List[SecurityFinding] = []

        # Run SAST (e.g., Bandit for Python code)
        # TODO: Add logic to determine language and run appropriate SAST tool
        # For now, assuming Python and running Bandit
        if True: # Add condition if project is Python
            logger.info(f"Initiating SAST scan for {project_id} at {project_code_path}")
            sast_findings = await self.run_sast_scan(project_id, project_code_path)
            all_findings.extend(sast_findings)
            self._publish_scan_results(event_id, project_id, commit_sha, None, "SAST_PYTHON", "bandit", 
                                       "COMPLETED_WITH_FINDINGS" if sast_findings else "COMPLETED_CLEAN", 
                                       sast_findings, f"{len(sast_findings)} SAST findings.")

        # Run Dependency Scan (e.g., Safety for Python)
        # TODO: Add logic for JS dependency scanning (npm audit, yarn audit) if JS projects are in scope
        if True: # Add condition if project is Python
            logger.info(f"Initiating Python dependency scan for {project_id} using {PYTHON_REQ_FILE_PATH}")
            dep_scan_findings = await self.run_dependency_scan_python(project_id, project_code_path) # project_code_path to find relative req file
            all_findings.extend(dep_scan_findings)
            self._publish_scan_results(event_id, project_id, commit_sha, None, "SCA_PYTHON", "safety",
                                       "COMPLETED_WITH_FINDINGS" if dep_scan_findings else "COMPLETED_CLEAN",
                                       dep_scan_findings, f"{len(dep_scan_findings)} dependency vulnerabilities.")
        
        logger.info(f"Total security findings for project {project_id}, commit {commit_sha}: {len(all_findings)}")


    async def handle_new_artifact(self, event: NewArtifactEvent):
        project_id = event["project_id"]
        commit_sha = event.get("commit_sha")
        artifact_name = event["artifact_name"]
        artifact_type = event["artifact_type"]
        artifact_location = event["artifact_location"] # This might be a Docker image name or S3 path
        event_id = event.get("event_id", str(uuid.uuid4()))

        logger.info(f"SecurityAgent handling NewArtifactEvent for {artifact_type} '{artifact_name}' from project {project_id}")

        findings: List[SecurityFinding] = []
        tool_name = "unknown_tool"
        scan_type_str = f"ARTIFACT_{artifact_type.upper()}"

        if artifact_type == "docker_image":
            tool_name = "trivy" # Example
            logger.info(f"Initiating container scan (Trivy) for image: {artifact_location}")
            # command = ["trivy", "image", "--format", "json", artifact_location]
            # scan_result = self._run_scan_tool(command)
            # if scan_result["status"] == "COMPLETED":
            #     findings = self._parse_trivy_results(scan_result["stdout"]) # Needs a parser
            # else:
            #    findings.append(SecurityFinding(finding_id=str(uuid.uuid4()), tool_name=tool_name, severity="CRITICAL", description=f"Container scan failed: {scan_result['status']}"))
            logger.warning("Trivy scan and parser not fully implemented in this V0.1.")
            findings.append(SecurityFinding(finding_id=str(uuid.uuid4()), tool_name=tool_name, severity="INFORMATIONAL", description=f"Placeholder: Trivy scan for {artifact_location} would run here."))


        self._publish_scan_results(event_id, project_id, commit_sha, artifact_name, 
                                   scan_type_str, tool_name, 
                                   "COMPLETED_WITH_FINDINGS" if findings else "COMPLETED_CLEAN", 
                                   findings, f"{len(findings)} findings for artifact {artifact_name}.")


    def _publish_scan_results(self, triggering_event_id: str, project_id: str, commit_sha: Optional[str],
                              artifact_name: Optional[str], scan_type: str, tool_name: str, status: str,
                              findings: List[SecurityFinding], summary: Optional[str]):
        if self.event_bus.redis_client:
            scan_result_event = SecurityScanResultEvent(
                event_type="SecurityScanResultEvent",
                triggering_event_id=triggering_event_id,
                project_id=project_id,
                commit_sha=commit_sha,
                artifact_name=artifact_name,
                scan_type=scan_type,
                tool_name=tool_name,
                status=status,
                findings=findings,
                summary=summary,
                # scan_duration_seconds= TBD,
                timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            )
            channel = SECURITY_SCAN_RESULT_CHANNEL_TEMPLATE.format(project_id=project_id)
            self.event_bus.publish(channel, scan_result_event)
            logger.info(f"Published SecurityScanResultEvent to {channel} for {scan_type} on {project_id or artifact_name}")


    async def main_event_loop(self):
        if not self.event_bus.redis_client:
            logger.critical("SecurityAgent: EventBus not connected. Worker cannot start.")
            await asyncio.sleep(60)
            return

        pubsub = self.event_bus.redis_client.pubsub()
        subscribed_ok_count = 0
        try:
            await asyncio.to_thread(pubsub.psubscribe, NEW_COMMIT_EVENT_CHANNEL_PATTERN)
            logger.info(f"SecurityAgent subscribed to pattern '{NEW_COMMIT_EVENT_CHANNEL_PATTERN}'")
            subscribed_ok_count +=1
            await asyncio.to_thread(pubsub.psubscribe, NEW_ARTIFACT_EVENT_CHANNEL_PATTERN)
            logger.info(f"SecurityAgent subscribed to pattern '{NEW_ARTIFACT_EVENT_CHANNEL_PATTERN}'")
            subscribed_ok_count +=1
        except redis.exceptions.RedisError as e:
            logger.critical(f"SecurityAgent: Failed to psubscribe to event patterns: {e}. Worker cannot start.")
            return
        
        if subscribed_ok_count == 0:
             logger.critical("SecurityAgent: No subscriptions successful. Worker exiting.")
             return

        logger.info(f"SecurityAgent worker started, listening for events...")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "pmessage":
                    channel = message["channel"]
                    event_data_str = message["data"]
                    logger.debug(f"SecurityAgent received raw message on channel '{channel}': {message_summary(event_data_str)}")
                    
                    # Start a new trace for each event
                    span_name = "security_agent.process_event_from_bus"
                    current_span_context = None # Placeholder for context propagation from message
                    
                    if not self.tracer_instance: # Fallback if tracer not initialized
                        await self._route_event(channel, event_data_str)
                        continue

                    with self.tracer_instance.start_as_current_span(span_name, context=current_span_context) as span:
                        span.set_attributes({
                            "messaging.system": "redis",
                            "messaging.destination.name": channel,
                            "messaging.operation": "process"
                        })
                        await self._route_event(channel, event_data_str, span)
                
                await asyncio.sleep(0.01) # Yield control
        except KeyboardInterrupt:
            logger.info("SecurityAgent event loop interrupted.")
        except Exception as e:
            logger.error(f"Critical error in SecurityAgent event loop: {e}", exc_info=True)
        finally:
            logger.info("SecurityAgent shutting down pubsub...")
            if pubsub:
                try: 
                    await asyncio.to_thread(pubsub.punsubscribe, NEW_COMMIT_EVENT_CHANNEL_PATTERN, NEW_ARTIFACT_EVENT_CHANNEL_PATTERN)
                    await asyncio.to_thread(pubsub.close)
                except Exception as e_close:
                     logger.error(f"Error closing pubsub for SecurityAgent: {e_close}")
            logger.info("SecurityAgent shutdown complete.")
    
    async def _route_event(self, channel: str, event_data_str: str, parent_span: Optional[trace.Span] = None):
        """Routes event to appropriate handler based on event_type within the event_data."""
        try:
            event_data = json.loads(event_data_str)
            event_type = event_data.get("event_type")

            if parent_span:
                parent_span.set_attribute("event.type", event_type or "unknown")
                parent_span.set_attribute("event.project_id", event_data.get("project_id", "N/A"))

            if event_type == "NewCommitEvent":
                # Basic validation for NewCommitEvent structure
                if all(k in event_data for k in ["project_id", "commit_sha", "changed_files"]):
                    await self.handle_new_commit(NewCommitEvent(**event_data))
                else:
                    logger.error(f"Malformed NewCommitEvent data for routing: {event_data_str[:200]}")
                    if parent_span: parent_span.set_status(trace.Status(trace.StatusCode.ERROR, "Malformed NewCommitEvent"))
            
            elif event_type == "NewArtifactEvent":
                # Basic validation for NewArtifactEvent
                if all(k in event_data for k in ["project_id", "artifact_name", "artifact_type", "artifact_location"]):
                    await self.handle_new_artifact(NewArtifactEvent(**event_data))
                else:
                    logger.error(f"Malformed NewArtifactEvent data for routing: {event_data_str[:200]}")
                    if parent_span: parent_span.set_status(trace.Status(trace.StatusCode.ERROR, "Malformed NewArtifactEvent"))
            else:
                logger.debug(f"SecurityAgent received event of unhandled type '{event_type}' on channel '{channel}'.")

        except json.JSONDecodeError:
            logger.error(f"Could not decode JSON for routing from channel '{channel}': {message_summary(event_data_str)}")
            if parent_span: parent_span.set_status(trace.Status(trace.StatusCode.ERROR, "JSONDecodeError in routing"))
        except TypeError as te: # For TypedDict unpacking
            logger.error(f"TypeError processing event for routing (mismatched keys?): {te} - Data: {event_data_str[:200]}")
            if parent_span: parent_span.set_status(trace.Status(trace.StatusCode.ERROR, "TypeError in routing"))
        except Exception as e:
            logger.error(f"Error routing event from channel '{channel}': {e}", exc_info=True)
            if parent_span: 
                parent_span.record_exception(e)
                parent_span.set_status(trace.Status(trace.StatusCode.ERROR, "Event routing logic failed"))


async def main_async_runner():
    agent = SecurityAgent()
    await agent.main_event_loop()

if __name__ == "__main__":
    # For example, to test locally, you might need to set CODE_BASE_MOUNT_PATH
    # and have some dummy project structure there.
    # e.g., os.environ["CODE_BASE_MOUNT_PATH"] = "./mock_codesrc"
    # And then publish a mock NewCommitEvent to Redis.
    try:
        asyncio.run(main_async_runner())
    except KeyboardInterrupt:
        logger.info("SecurityAgent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"SecurityAgent failed to start or unhandled error in __main__: {e}", exc_info=True)
```
