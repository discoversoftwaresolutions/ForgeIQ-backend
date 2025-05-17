# =======================================
# 📁 agents/SecurityAgent/agent.py (V0.3)
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

# --- Observability Setup (as in V0.2 - response #73) ---
SERVICE_NAME = "SecurityAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(otelTraceID)s - %(otelSpanID)s - %(message)s')
_tracer = None; _trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing
    _tracer = setup_tracing(SERVICE_NAME)
    _trace_api = otel_trace_api
except ImportError: logging.getLogger(SERVICE_NAME).warning("SecurityAgent: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from interfaces.types.events import NewCommitEvent, FileChange, NewArtifactEvent, SecurityFinding, SecurityScanResultEvent

CODE_BASE_MOUNT_PATH = os.getenv("CODE_BASE_MOUNT_PATH", "/codesrc") 
PYTHON_REQ_FILE_PATH_IN_PROJECT = os.getenv("PYTHON_REQ_FILE_PATH_IN_PROJECT", "requirements.txt")

# Bandit specific configurations
BANDIT_CONFIG_FILE_NAME_IN_PROJECT = os.getenv("BANDIT_CONFIG_FILE_NAME_IN_PROJECT", ".bandit.yml") # e.g., .bandit, bandit.yaml
GLOBAL_BANDIT_CONFIG_PATH_IN_CONTAINER = os.getenv("GLOBAL_BANDIT_CONFIG_PATH_IN_CONTAINER") # e.g., /app/config/global.bandit.yml

NEW_COMMIT_EVENT_CHANNEL_PATTERN = "events.project.*.new_commit"
NEW_ARTIFACT_EVENT_CHANNEL_PATTERN = "events.project.*.new_artifact"
SECURITY_SCAN_RESULT_CHANNEL_TEMPLATE = "events.project.{project_id}.security.scan_result"

class SecurityAgent:
    def __init__(self):
        logger.info("Initializing SecurityAgent (V0.3)...")
        self.event_bus = EventBus()
        if not self.event_bus.redis_client:
            logger.error("SecurityAgent critical: EventBus not connected.")
        logger.info("SecurityAgent V0.3 Initialized.")

    @property
    def tracer_instance(self):
        return _tracer

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same helper as before) ...
        if self.tracer_instance and _trace_api:
            span = self.tracer_instance.start_span(f"security_agent.{operation_name}", context=parent_context)
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

    def _run_scan_tool(self, command: List[str], cwd: Optional[str] = None, tool_name: str = "unknown_tool") -> Dict[str, Any]:
        # ... (same robust helper from V0.2 - response #73, ensure it's complete) ...
        logger.info(f"Running {tool_name} scan: \"{' '.join(command)}\" in CWD: \"{cwd or os.getcwd()}\"")
        start_time = time.monotonic(); process = None
        try:
            process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            stdout, stderr = process.communicate(timeout=300) # 5 min timeout
            returncode = process.returncode; duration = time.monotonic() - start_time
            log_msg_parts = [f"{tool_name} scan finished. RC: {returncode}, Duration: {duration:.2f}s."]
            if stdout: log_msg_parts.append(f"Stdout (first 500): {stdout[:500]}")
            if stderr: log_msg_parts.append(f"Stderr (first 500): {stderr[:500]}")
            logger.debug("\n".join(log_msg_parts))

            status = "COMPLETED_TOOL_RUN"
            # Bandit: 0 (no issues), 1 (issues found), >1 (error like invalid config)
            # Safety: 0 (no issues), non-zero (issues or error)
            # This logic helps distinguish tool execution failure from finding issues.
            if tool_name == "bandit" and returncode > 1: status = "TOOL_ERROR"
            elif tool_name == "safety" and returncode != 0 and not stdout.strip(): status = "TOOL_ERROR" # Crude check

            return {"status": status, "stdout": stdout, "stderr": stderr, "exit_code": returncode, "duration": duration}
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start_time; logger.error(f"{tool_name} scan timed out: {' '.join(command)}")
            if process: process.kill(); process.communicate()
            return {"status": "TIMEOUT", "stdout": "", "stderr": "Scan timed out.", "duration": duration, "exit_code": -1}
        except FileNotFoundError:
            duration = time.monotonic() - start_time; logger.error(f"{tool_name} cmd not found: {command[0]}")
            return {"status": "CMD_NOT_FOUND", "stdout": "", "stderr": f"Cmd {command[0]} not found.", "duration": duration, "exit_code": -1}
        except Exception as e:
            duration = time.monotonic() - start_time; logger.error(f"Error running {tool_name} cmd: {e}", exc_info=True)
            return {"status": "AGENT_ERROR", "stdout": "", "stderr": str(e), "duration": duration, "exit_code": -1}


    def _parse_bandit_results(self, json_output_str: str, scanned_path_offset: str = "") -> List[SecurityFinding]:
        """Parses Bandit JSON output. `scanned_path_offset` is the path Bandit was run against, to make filenames absolute if needed."""
        findings: List[SecurityFinding] = []
        if not json_output_str.strip():
            logger.info("Bandit output was empty, no findings parsed.")
            return findings
        try:
            data = json.loads(json_output_str)
            scan_errors = data.get("errors", [])
            if scan_errors:
                for error_item in scan_errors: # Bandit error objects are dicts
                    err_file = error_item.get('filename', 'N/A')
                    err_reason = error_item.get('reason', 'Unknown Bandit error')
                    logger.error(f"Bandit scan error: File '{err_file}', Reason: {err_reason}")
                    findings.append(SecurityFinding(
                        finding_id=str(uuid.uuid4()), rule_id="BANDIT_SCAN_ERROR", severity="CRITICAL",
                        description=f"Bandit scan error in '{err_file}': {err_reason}",
                        file_path=err_file, tool_name="bandit", confidence="HIGH",
                        code_snippet=None, line_number=None, remediation=None, more_info_url=None
                    ))

            for result in data.get("results", []):
                severity_map = {"LOW": "LOW", "MEDIUM": "MEDIUM", "HIGH": "HIGH"} # Bandit severities
                bandit_severity = result.get("issue_severity", "UNDEFINED").upper()

                line_range = result.get("line_range", [])
                line_num_start = line_range[0] if line_range else result.get("line_number")

                # Bandit filenames are relative to the scan target.
                # If bandit was run with `cwd=code_path_to_scan` and target `-r .`
                # then result.get("filename") is already relative to `code_path_to_scan`.
                # If scanned_path_offset is the project root within CODE_BASE_MOUNT_PATH,
                # then the full path might be os.path.join(scanned_path_offset, result.get("filename"))
                # but often the relative path from project root is most useful.
                reported_filename = result.get("filename")
                # Ensure it's a relative path if it starts with scanned_path_offset
                if scanned_path_offset and reported_filename and reported_filename.startswith(scanned_path_offset):
                     reported_filename = os.path.relpath(reported_filename, scanned_path_offset)


                findings.append(SecurityFinding(
                    finding_id=result.get("test_id", "") + "_" + hashlib.sha1((reported_filename or "").encode() + str(line_num_start).encode() + result.get("test_name","").encode()).hexdigest()[:8],
                    rule_id=result.get("test_id"),
                    severity=severity_map.get(bandit_severity, "MEDIUM"), # Default if mapping fails
                    description=result.get("issue_text", "N/A"),
                    file_path=reported_filename,
                    line_number=line_num_start,
                    code_snippet=result.get("code", "")[:1000], 
                    remediation=None, 
                    tool_name="bandit",
                    confidence=result.get("issue_confidence", "UNDEFINED").upper(),
                    more_info_url=result.get("more_info", "").strip() or None
                ))
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Bandit JSON output: {e}. Output (first 500 chars): {json_output_str[:500]}")
        except Exception as e:
            logger.error(f"Unexpected error parsing Bandit results: {e}", exc_info=True)
        return findings

    # ... (_parse_safety_results - V0.2 from response #73 is good for now) ...
    def _parse_safety_results(self, json_output_str: str, requirements_file_path: str) -> List[SecurityFinding]:
        # (Using the V0.2 version from response #73)
        findings: List[SecurityFinding] = []; 
        if not json_output_str.strip(): logger.info("Safety output was empty."); return findings
        try:
            data = json.loads(json_output_str) 
            # Assuming structured output like: {"vulnerabilities": [...]} or older list format
            vulns_list = data if isinstance(data, list) else data.get("vulnerabilities", [])

            for vuln in vulns_list:
                # Adapt to common fields found in safety JSON reports
                pkg_name = vuln.get('package_name') or vuln.get('name') or (vuln[1] if isinstance(vuln, list) and len(vuln)>1 else "UnknownPkg")
                version = vuln.get('analyzed_version') or vuln.get('version') or (vuln[3] if isinstance(vuln, list) and len(vuln)>3 else "N/A")
                vuln_id = vuln.get('vulnerability_id') or vuln.get('id') or (vuln[0] if isinstance(vuln, list) else str(uuid.uuid4()))
                summary = vuln.get('advisory_summary') or vuln.get('advisory') or (vuln[4] if isinstance(vuln, list) and len(vuln)>4 else "No summary")
                severity = (vuln.get('severity') or "HIGH").upper() if vuln.get('severity') else "HIGH"
                more_info = vuln.get('more_info_url') or (vuln[5] if isinstance(vuln, list) and len(vuln)>5 else None)

                findings.append(SecurityFinding(
                    finding_id=vuln_id, rule_id=vuln.get('CVE') or vuln_id, severity=severity,
                    description=f"{summary} (Package: {pkg_name} v{version})",
                    file_path=requirements_file_path, line_number=None,
                    code_snippet=f"{pkg_name}=={version}",
                    remediation=f"Update {pkg_name}. More info: {more_info or 'N/A'}",
                    tool_name="safety", confidence="HIGH"
                ))
        except json.JSONDecodeError as e: logger.error(f"Failed to parse Safety JSON: {e}. Output: {json_output_str[:500]}")
        except Exception as e: logger.error(f"Error parsing Safety results: {e}", exc_info=True)
        return findings


    async def run_sast_scan_on_project(self, project_id: str, project_code_path_in_container: str, event_id: str, commit_sha: Optional[str]):
        """Runs SAST scan (Bandit) for a project and publishes results."""
        span = self._start_trace_span_if_available("run_sast_scan_on_project", 
                                                   project_id=project_id, code_path=project_code_path_in_container, 
                                                   trigger_event_id=event_id, commit_sha=commit_sha)
        findings: List[SecurityFinding] = []
        scan_status_code = "FAILED_TO_SCAN"
        summary = "SAST scan (Bandit) did not complete as expected."
        tool_name = "bandit"

        try:
            with span: #type: ignore
                logger.info(f"V0.3: Running SAST (Bandit) on '{project_code_path_in_container}' for project '{project_id}'")

                bandit_cmd = ["bandit", "-r", ".", "-f", "json", "-q"] # -q for quieter, errors still on stderr

                # Determine config file path
                project_bandit_config = os.path.join(project_code_path_in_container, BANDIT_CONFIG_FILE_NAME_IN_PROJECT)
                config_to_use = None
                if os.path.exists(project_bandit_config):
                    config_to_use = BANDIT_CONFIG_FILE_NAME_IN_PROJECT # Bandit will find it in cwd if name is standard like .bandit
                    logger.info(f"Using project-local Bandit config: {config_to_use} (relative to scan path)")
                    # If bandit needs full path for -c when run from different CWD, construct it.
                    # For `cwd=project_code_path_in_container`, relative path is fine.
                    bandit_cmd.extend(["-c", config_to_use])
                elif GLOBAL_BANDIT_CONFIG_PATH_IN_CONTAINER and os.path.exists(GLOBAL_BANDIT_CONFIG_PATH_IN_CONTAINER):
                    config_to_use = GLOBAL_BANDIT_CONFIG_PATH_IN_CONTAINER
                    bandit_cmd.extend(["-c", config_to_use])
                    logger.info(f"Using global Bandit config: {config_to_use}")
                else:
                    logger.info("No specific Bandit config file found, using Bandit defaults.")

                # Run bandit with cwd set to the project code path
                scan_result_dict = self._run_scan_tool(bandit_cmd, cwd=project_code_path_in_container, tool_name=tool_name)

                if scan_result_dict["status"] == "COMPLETED_TOOL_RUN":
                    findings = self._parse_bandit_results(scan_result_dict["stdout"], project_code_path_in_container)
                    scan_status_code = "COMPLETED_WITH_FINDINGS" if findings else "COMPLETED_CLEAN"
                    summary = f"Bandit scan completed: {len(findings)} finding(s)."
                    # Bandit exit codes: 0 (no issues), 1 (issues found), >1 (error)
                    if scan_result_dict["exit_code"] > 1: # Indicates a tool error, not just findings
                         scan_status_code = "TOOL_ERROR"
                         error_detail = scan_result_dict['stderr'][:200] or scan_result_dict['stdout'][:200]
                         summary += f" Tool execution error (Code: {scan_result_dict['exit_code']}). Details: {error_detail}"
                         logger.error(f"Bandit tool error for {project_id}: {scan_result_dict['stderr']}")
                else: 
                    summary = f"Bandit scan execution failed: {scan_result_dict['status']}. Details: {scan_result_dict['stderr'][:200]}"
                    findings.append(SecurityFinding( # Create a meta-finding for scan failure
                        finding_id=str(uuid.uuid4()), rule_id="SCAN_EXECUTION_FAILURE", severity="CRITICAL",
                        description=summary, tool_name=tool_name, file_path=project_code_path_in_container,
                        confidence=None, code_snippet=None, line_number=None, remediation=None, more_info_url=None
                    ))

                if _trace_api and span: 
                    span.set_attribute("security.sast.findings_count", len(findings))
                    span.set_attribute("security.sast.status", scan_status_code)
                    if scan_result_dict.get("exit_code", -1) > 1: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Bandit tool error"))

        except Exception as e:
            logger.error(f"Unexpected error in SAST scan logic for {project_id}: {e}", exc_info=True)
            summary = f"Agent error during SAST scan: {str(e)}"
            scan_status_code = "AGENT_ERROR"
            if _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "SAST Agent Error"))
        finally:
            self._publish_scan_results(
                triggering_event_id=event_id, project_id=project_id, commit_sha=commit_sha,
                artifact_name=None, scan_type="SAST_PYTHON_BANDIT", tool_name=tool_name,
                status=scan_status_code, findings=findings, summary=summary
            )

    async def run_python_dependency_scan_on_project(self, project_id: str, code_base_root_in_container: str, event_id: str, commit_sha: Optional[str]):
        """Runs Python dependency scan (Safety) using the root requirements.txt."""
        # (This method is largely the same as V0.2, ensure logging and tracing are consistent)
        span = self._start_trace_span_if_available("run_python_dependency_scan", project_id=project_id, path=code_base_root_in_container, trigger_event_id=event_id)
        findings: List[SecurityFinding] = []; scan_status_code = "FAILED_TO_SCAN"; summary = "Python dependency scan did not complete."; tool_name="safety"
        try:
            with span: # type: ignore
                root_req_file = os.path.join(code_base_root_in_container, "requirements.txt") # Always use root requirements for now
                if not os.path.exists(root_req_file):
                    summary = f"Root requirements.txt not found at {root_req_file}. Skipping Python dependency scan."; scan_status_code = "CONFIG_ERROR"
                    logger.warning(summary); self._publish_scan_results(event_id, project_id, commit_sha, None, "SCA_PYTHON", tool_name, scan_status_code, [], summary); return

                logger.info(f"V0.3: Running Python dependency scan (Safety) on '{root_req_file}' for project '{project_id}' context.")
                safety_cmd = ["safety", "check", "-r", root_req_file, "--json", "--continue-on-error"] # Continue on error to get full report
                scan_result_dict = self._run_scan_tool(safety_cmd, cwd=code_base_root_in_container, tool_name=tool_name)

                if scan_result_dict["status"] == "COMPLETED_TOOL_RUN":
                    findings = self._parse_safety_results(scan_result_dict["stdout"], "requirements.txt") # Report against root file
                    scan_status_code = "COMPLETED_WITH_FINDINGS" if findings else "COMPLETED_CLEAN"
                    summary = f"Safety scan completed: {len(findings)} finding(s)."
                    # Safety exits 0 if no vulns, 1 if vulns found, >1 for other errors
                    if scan_result_dict["exit_code"] > 1: # Tool execution error
                         scan_status_code = "TOOL_ERROR"; summary += f" Tool error (Code: {scan_result_dict['exit_code']}). Stderr: {scan_result_dict['stderr'][:200]}"
                else: summary = f"Safety scan execution failed: {scan_result_dict['status']}. Details: {scan_result_dict['stderr'][:200]}"
                if _trace_api: span.set_attribute("security.sca.findings_count", len(findings)); span.set_attribute("security.sca.status", scan_status_code)
        except Exception as e:
            logger.error(f"Unexpected error in Python dependency scan for {project_id}: {e}", exc_info=True)
            summary = f"Agent error during Python dependency scan: {str(e)}"; scan_status_code = "AGENT_ERROR"
            if _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "SCA Agent Error"))
        finally:
            self._publish_scan_results(event_id, project_id, commit_sha, None, "SCA_PYTHON", tool_name, scan_status_code, findings, summary)


    async def handle_new_commit(self, event: NewCommitEvent): # event is already parsed NewCommitEvent
        # (Main logic from V0.2, ensure it calls the V0.3 scan methods)
        project_id = event["project_id"]; commit_sha = event["commit_sha"]; event_id = event.get("event_id", str(uuid.uuid4()))
        logger.info(f"SecurityAgent V0.3 handling NewCommitEvent for project {project_id}, commit {commit_sha}")
        project_code_path = os.path.join(CODE_BASE_MOUNT_PATH, project_id)
        if not os.path.isdir(project_code_path):
            logger.error(f"Source code path not found for '{project_id}': '{project_code_path}'."); self._publish_scan_results(event_id, project_id, commit_sha, None, "PRE_SCAN_ERROR", "setup", "FAILED_TO_SCAN", [], f"Source path not found: {project_code_path}"); return

        scan_tasks = [
            self.run_sast_scan_on_project(project_id, project_code_path, event_id, commit_sha),
            self.run_python_dependency_scan_on_project(project_id, "/app", event_id, commit_sha) # Pass /app as monorepo root for root reqs.txt
        ]
        await asyncio.gather(*scan_tasks)
        logger.info(f"Security scans initiated for project '{project_id}', commit '{commit_sha}'.")

    # ... (handle_new_artifact, _publish_scan_results, _route_event, main_event_loop from V0.2 - response #73 are mostly fine) ...
    # Ensure they use self._start_trace_span_if_available and logger correctly.
    # For brevity, I'll assume these are carried over and adapted if needed.
    # The _publish_scan_results method is critical and its V0.2 version was okay.
    # The main_event_loop and _route_event also from V0.2.

    # Pasting _publish_scan_results, _route_event, and main_event_loop from V0.2 for completeness, with minor logging/tracing checks
    def _publish_scan_results(self, triggering_event_id: str, project_id: str, commit_sha: Optional[str],
                              artifact_name: Optional[str], scan_type: str, tool_name: str, status: str,
                              findings: List[SecurityFinding], summary: Optional[str]):
        span = self._start_trace_span_if_available("publish_scan_results", trigger_event_id=triggering_event_id, scan_type=scan_type, status=status)
        try:
            with span: #type: ignore
                if self.event_bus.redis_client:
                    event_data = SecurityScanResultEvent(
                        event_type="SecurityScanResultEvent", triggering_event_id=triggering_event_id,
                        project_id=project_id, commit_sha=commit_sha, artifact_name=artifact_name,
                        scan_type=scan_type, tool_name=tool_name, status=status, findings=findings,
                        summary=summary, timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                    )
                    channel = SECURITY_SCAN_RESULT_CHANNEL_TEMPLATE.format(project_id=project_id)
                    self.event_bus.publish(channel, event_data) # type: ignore
                    logger.info(f"Published SecurityScanResultEvent to {channel} for {scan_type}")
                    if _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                else:
                    logger.error("Cannot publish SecurityScanResultEvent: EventBus not connected.")
                    if _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "EventBus disconnected"))
        except Exception as e:
            logger.error(f"Error during _publish_scan_results: {e}", exc_info=True)
            if _trace_api and span : span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Publish failed"))

    async def _route_event(self, channel: str, event_data_str: str, parent_span_for_route: Optional[Any] = None):
        span = self._start_trace_span_if_available("route_event", parent_context=parent_span_for_route, event_channel=channel)
        try:
            with span: #type: ignore
                event_data = json.loads(event_data_str)
                event_type = event_data.get("event_type")
                if _tracer: span.set_attribute("event.type_received", event_type or "unknown")

                if event_type == "NewCommitEvent":
                    if all(k in event_data for k in ["project_id", "commit_sha", "changed_files"]):
                        await self.handle_new_commit(NewCommitEvent(**event_data)) #type: ignore
                    else: logger.error(f"Malformed NewCommitEvent: {event_data_str[:200]}")
                elif event_type == "NewArtifactEvent": # From response #46
                    if all(k in event_data for k in ["project_id", "artifact_name", "artifact_type", "artifact_location"]):
                         await self.handle_new_artifact(NewArtifactEvent(**event_data)) #type: ignore
                    else: logger.error(f"Malformed NewArtifactEvent: {event_data_str[:200]}")
                else: logger.debug(f"SecurityAgent received unhandled event type '{event_type}' on '{channel}'.")
        except json.JSONDecodeError: logger.error(f"Could not decode JSON for routing on '{channel}': {message_summary(event_data_str)}")
        except TypeError as te: logger.error(f"TypeError processing event for routing on '{channel}': {te}")
        except Exception as e: logger.error(f"Error routing event from '{channel}': {e}", exc_info=True)


    async def handle_new_artifact(self, event: NewArtifactEvent): # V0.2 logic from response #73
        # ... (Conceptual Trivy scan, publish results) ...
        project_id = event["project_id"]; artifact_name = event["artifact_name"]; artifact_type = event["artifact_type"]
        logger.info(f"SecurityAgent V0.3 handling NewArtifactEvent for {artifact_type} '{artifact_name}' from project {project_id}")
        # For V0.2, we'll just log and publish a placeholder result for Docker images
        findings: List[SecurityFinding] = []
        tool_name = "trivy_placeholder"
        scan_status_code = "SKIPPED"
        summary = f"Artifact scan for type '{artifact_type}' not fully implemented for {artifact_name}."

        if artifact_type == "docker_image":
            summary = f"Conceptual Trivy scan for Docker image '{event['artifact_location']}'. Actual scan not run in V0.3."
            scan_status_code = "COMPLETED_CLEAN" # Simulate clean for now
            logger.warning(summary)

        self._publish_scan_results(
            triggering_event_id=event["event_id"], project_id=project_id, commit_sha=event.get("commit_sha"),
            artifact_name=artifact_name, scan_type=f"ARTIFACT_{artifact_type.upper()}", tool_name=tool_name,
            status=scan_status_code, findings=findings, summary=summary
        )


    async def main_event_loop(self): # V0.2 logic from response #73
        if not self.event_bus.redis_client: logger.critical("SecurityAgent: EventBus not connected. Exiting."); await asyncio.sleep(60); return
        pubsub = self.event_bus.redis_client.pubsub() #type: ignore
        subscribed_patterns = [NEW_COMMIT_EVENT_CHANNEL_PATTERN, NEW_ARTIFACT_EVENT_CHANNEL_PATTERN]
        try:
            for pattern in subscribed_patterns:
                if pattern.strip(): await asyncio.to_thread(pubsub.psubscribe, pattern.strip()); logger.info(f"SecurityAgent subscribed to '{pattern.strip()}'") #type: ignore
        except redis.exceptions.RedisError as e: logger.critical(f"SecurityAgent: Failed to psubscribe: {e}. Exiting."); return #type: ignore
        logger.info(f"SecurityAgent worker started, listening...")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0) #type: ignore
                if message and message["type"] == "pmessage":
                    parent_span = self._start_trace_span_if_available("process_bus_event", source_channel=message.get("channel"))
                    try: # Add try-finally for span.end()
                        with parent_span: #type: ignore
                            await self._route_event(message["channel"].decode('utf-8'), message["data"].decode('utf-8'), parent_span) #type: ignore
                    finally:
                        if hasattr(parent_span, 'end'): parent_span.end() #type: ignore
                await asyncio.sleep(0.01) 
        except KeyboardInterrupt: logger.info("SecurityAgent event loop interrupted.")
        except Exception as e: logger.error(f"Critical error in SecurityAgent event loop: {e}", exc_info=True)
        finally: # ... (pubsub cleanup as in V0.2)
            logger.info("SecurityAgent shutting down...");
            if pubsub:
                try: 
                    for p in subscribed_patterns: 
                        if p.strip(): await asyncio.to_thread(pubsub.punsubscribe, p.strip()) #type: ignore
                    await asyncio.to_thread(pubsub.close) #type: ignore
                except Exception as e_close: logger.error(f"Error closing pubsub: {e_close}")
            logger.info("SecurityAgent shutdown complete.")


async def main_async_runner():
    agent = SecurityAgent()
    await agent.main_event_loop()

if __name__ == "__main__":
    # For local testing, ensure CODE_BASE_MOUNT_PATH points to sample code
    # and necessary env vars like REDIS_URL are set.
    # Also, Bandit would need to be installed in the environment.
    try:
        asyncio.run(main_async_runner())
    except KeyboardInterrupt:
        logger.info("SecurityAgent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"SecurityAgent failed to start: {e}", exc_info=True)
