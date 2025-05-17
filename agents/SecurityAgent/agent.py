# =======================================
# 📁 agents/SecurityAgent/app/agent.py (V0.3)
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

# --- Observability Setup (as in V0.2) ---
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
# For V0.3, let's assume dependency scan targets the monorepo's root requirements.txt
# or one found at a standard path within the project being scanned.
# If scanning a specific project's deps:
# PYTHON_REQ_FILE_NAME = os.getenv("PYTHON_REQ_FILE_NAME", "requirements.txt")
# For now, we'll assume the scan targets the main requirements.txt at /app/requirements.txt

BANDIT_CONFIG_FILE_NAME_IN_PROJECT = os.getenv("BANDIT_CONFIG_FILE_NAME_IN_PROJECT", ".bandit.yml")
GLOBAL_BANDIT_CONFIG_PATH_IN_CONTAINER = os.getenv("GLOBAL_BANDIT_CONFIG_PATH_IN_CONTAINER")

NEW_COMMIT_EVENT_CHANNEL_PATTERN = "events.project.*.new_commit"
NEW_ARTIFACT_EVENT_CHANNEL_PATTERN = "events.project.*.new_artifact"
SECURITY_SCAN_RESULT_CHANNEL_TEMPLATE = "events.project.{project_id}.security.scan_result"

class SecurityAgent:
    def __init__(self):
        # ... (same as V0.2 from response #73) ...
        logger.info("Initializing SecurityAgent (V0.3)...")
        self.event_bus = EventBus()
        if not self.event_bus.redis_client:
            logger.error("SecurityAgent critical: EventBus not connected.")
        logger.info("SecurityAgent V0.3 Initialized.")

    @property
    def tracer_instance(self): return _tracer # Keep this consistent

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same helper as V0.2 from response #73) ...
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
        # ... (same robust helper from V0.2/V0.3 - response #73) ...
        logger.info(f"Running {tool_name} scan: \"{' '.join(command)}\" in CWD: \"{cwd or os.getcwd()}\"")
        start_time = time.monotonic(); process = None
        try:
            process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            stdout, stderr = process.communicate(timeout=300)
            returncode = process.returncode; duration = time.monotonic() - start_time
            log_msg_parts = [f"{tool_name} scan finished. RC: {returncode}, Duration: {duration:.2f}s."]
            if stdout: log_msg_parts.append(f"Stdout (first 500): {stdout[:500]}")
            if stderr: log_msg_parts.append(f"Stderr (first 500): {stderr[:500]}")
            logger.debug("\n".join(log_msg_parts))
            status = "COMPLETED_TOOL_RUN"
            if tool_name == "bandit" and returncode > 1 : status = "TOOL_ERROR"
            elif tool_name == "safety" and returncode != 0: # Safety exits non-zero if vulns are found OR on error
                # We need to check stdout for JSON to distinguish "found vulns" from "tool error"
                try:
                    json.loads(stdout) # If this parses, it means safety ran and outputted JSON (even if empty)
                    if not stdout.strip() or stdout.strip() == "[]" or stdout.strip() == "{}": # No vulns but non-zero exit? Maybe config issue.
                         if stderr: status = "TOOL_ERROR" # If stderr has content, likely a tool error
                    # If it has vulns, status remains COMPLETED_TOOL_RUN, findings determine severity
                except json.JSONDecodeError:
                    if stderr: status = "TOOL_ERROR" # Could not parse JSON and stderr exists, likely tool error
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
        # ... (same as V0.3 from response #73, ensure it's complete and correct) ...
        findings: List[SecurityFinding] = []
        if not json_output_str.strip(): logger.info("Bandit output was empty."); return findings
        try:
            data = json.loads(json_output_str)
            for error_item in data.get("errors", []):
                logger.error(f"Bandit scan error: File '{error_item.get('filename', 'N/A')}' - {error_item.get('reason', 'Unknown')}")
                findings.append(SecurityFinding(finding_id=str(uuid.uuid4()), rule_id="BANDIT_SCAN_ERROR", severity="CRITICAL", description=f"Bandit scan error in '{error_item.get('filename', 'N/A')}': {error_item.get('reason', 'Unknown')}", file_path=error_item.get('filename'), tool_name="bandit", confidence="HIGH", code_snippet=None, line_number=None, remediation=None, more_info_url=None))
            for result in data.get("results", []):
                severity_map = {"LOW": "LOW", "MEDIUM": "MEDIUM", "HIGH": "HIGH"}
                bandit_severity = result.get("issue_severity", "MEDIUM").upper()
                line_range = result.get("line_range", []); line_num_start = line_range[0] if line_range else result.get("line_number")
                reported_filename = result.get("filename") # Path relative to bandit's CWD
                findings.append(SecurityFinding(
                    finding_id=result.get("test_id", "") + "_" + hashlib.sha1((reported_filename or "").encode() + str(line_num_start).encode() + result.get("test_name","").encode()).hexdigest()[:8],
                    rule_id=result.get("test_id"), severity=severity_map.get(bandit_severity, "MEDIUM"),
                    description=result.get("issue_text", "N/A"), file_path=reported_filename, line_number=line_num_start,
                    code_snippet=result.get("code", "")[:1000], remediation=None, tool_name="bandit",
                    confidence=result.get("issue_confidence", "UNDEFINED").upper(), more_info_url=result.get("more_info", "").strip() or None))
        except json.JSONDecodeError as e: logger.error(f"Failed to parse Bandit JSON: {e}. Output: {json_output_str[:500]}")
        except Exception as e: logger.error(f"Unexpected error parsing Bandit results: {e}", exc_info=True)
        return findings

    def _parse_safety_results_v2(self, json_output_str: str, requirements_file_path: str) -> List[SecurityFinding]:
        """
        Enhanced parser for `safety check --json` output.
        Safety's JSON output (e.g., version 2.x.x, 3.x.x) is typically a dictionary where keys are package names,
        and values are lists of vulnerability details for that package.
        Older versions might output a simple list of [vuln_id, package, spec, version, advisory, cve].
        This parser attempts to handle the more structured dictionary output first.
        """
        findings: List[SecurityFinding] = []
        if not json_output_str.strip():
            logger.info("Safety output was empty, no findings parsed.")
            return findings
        try:
            data = json.loads(json_output_str)

            # Safety new JSON format is a list of vulnerabilities directly
            # [ [ "requests", "<2.25.1", "2.24.0", "Requests before 2.25.1 allows SSRF via request.get", "43584", null ], ... ]
            # The dict format was for older versions or different commands.
            # `safety check -r requirements.txt --json` outputs a list of lists.
            if isinstance(data, list): # Standard output for `safety check --json`
                for vuln_info in data:
                    if len(vuln_info) >= 5: # [pkg, spec, installed_ver, advisory, vuln_id, cve (optional)]
                        package_name = vuln_info[0]
                        affected_spec = vuln_info[1]
                        installed_version = vuln_info[2]
                        advisory = vuln_info[3]
                        vuln_db_id = vuln_info[4] # Safety's internal ID for the vulnerability
                        cve = vuln_info[5] if len(vuln_info) > 5 else None

                        # Severity is not directly provided by safety in this list format, default to HIGH
                        severity = "HIGH" 
                        # Try to infer severity from keywords in advisory if desired (e.g., "critical", "high")
                        if "critical" in advisory.lower(): severity = "CRITICAL"
                        elif "moderate" in advisory.lower(): severity = "MEDIUM" # less common
                        elif "low" in advisory.lower(): severity = "LOW"

                        description = f"{advisory} (Package: {package_name}, Version: {installed_version}, Affected: {affected_spec})"

                        findings.append(SecurityFinding(
                            finding_id=vuln_db_id,
                            rule_id=cve or vuln_db_id, # Prefer CVE if available
                            severity=severity,
                            description=description,
                            file_path=requirements_file_path, # The file that was scanned
                            line_number=None, # Not applicable for dependency scan line items
                            code_snippet=f"{package_name}=={installed_version}", # The vulnerable package
                            remediation=f"Update package '{package_name}' to a version not matching '{affected_spec}'. More info: CVE-{cve if cve else 'N/A'} or check Safety DB ID {vuln_db_id}.",
                            tool_name="safety",
                            confidence="HIGH" # Findings from dependency DB are generally high confidence
                        ))
            else:
                logger.warning(f"Safety JSON output was not in the expected list format. Output: {json_output_str[:500]}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Safety JSON output: {e}. Output (first 500 chars): {json_output_str[:500]}")
        except Exception as e:
            logger.error(f"Unexpected error parsing Safety results: {e}", exc_info=True)
        return findings

    async def run_sast_scan_on_project(self, project_id: str, project_code_path_in_container: str, event_id: str, commit_sha: Optional[str]):
        # ... (This method from V0.2 (response #73) is largely good, ensure it uses the updated _parse_bandit_results) ...
        # ... and the updated _run_scan_tool which distinguishes COMPLETED_TOOL_RUN from TOOL_ERROR ...
        # I will ensure this method's logic is consistent with the improved helpers.
        span = self._start_trace_span_if_available("run_sast_scan_on_project", project_id=project_id, code_path=project_code_path_in_container)
        findings: List[SecurityFinding] = []; scan_status_code = "FAILED_TO_SCAN"; summary = "SAST (Bandit) did not complete."; tool_name = "bandit"
        try:
            with span: #type: ignore
                logger.info(f"V0.3 SAST (Bandit) on '{project_code_path_in_container}' for '{project_id}'")
                bandit_cmd = ["bandit", "-r", ".", "-f", "json", "-q"] # Scan current dir, which is project_code_path_in_container
                config_path_to_use = None
                proj_bandit_cfg = os.path.join(project_code_path_in_container, BANDIT_CONFIG_FILE_NAME_IN_PROJECT)
                if os.path.exists(proj_bandit_cfg): config_path_to_use = BANDIT_CONFIG_FILE_NAME_IN_PROJECT # Relative to CWD
                elif GLOBAL_BANDIT_CONFIG_PATH_IN_CONTAINER and os.path.exists(GLOBAL_BANDIT_CONFIG_PATH_IN_CONTAINER): config_path_to_use = GLOBAL_BANDIT_CONFIG_PATH_IN_CONTAINER
                if config_path_to_use: bandit_cmd.extend(["-c", config_path_to_use]); logger.info(f"Using Bandit config: {config_path_to
