# =================================================
# 📁 agents/CI_CD_Agent/agent.py (V0.3 Enhanced)
# =================================================
import os
import json
import datetime
import uuid
import asyncio
import logging
import subprocess
import httpx 
import time # For polling
import re # For parsing CLI output
from typing import Dict, Any, Optional, List

# --- Observability Setup (as before) ---
SERVICE_NAME = "CI_CD_Agent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s')
_tracer = None; _trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing
    _tracer = setup_tracing(SERVICE_NAME)
    _trace_api = otel_trace_api
except ImportError: logging.getLogger(SERVICE_NAME).warning("CI_CD_Agent: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from core.shared_memory import SharedMemoryStore # <<< IMPORT SharedMemoryStore
from interfaces.types.events import DeploymentRequestEvent, DeploymentStatusEvent

# Railway Configuration (as before)
RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN")
RAILWAY_PROJECT_ID = os.getenv("RAILWAY_PROJECT_ID")
RAILWAY_API_BASE_URL = os.getenv("RAILWAY_API_BASE_URL", "https://api.railway.app")
DEPLOYMENT_POLL_INTERVAL_SECONDS = int(os.getenv("DEPLOYMENT_POLL_INTERVAL_SECONDS", "30"))
DEPLOYMENT_POLL_TIMEOUT_SECONDS = int(os.getenv("DEPLOYMENT_POLL_TIMEOUT_SECONDS", "1800"))

# Event channels (as before)
DEPLOYMENT_REQUEST_CHANNEL_PATTERN = "events.project.*.deployment.request"
DEPLOYMENT_STATUS_CHANNEL_TEMPLATE = "events.project.{project_id}.service.{service_name}.deployment.status"

# Shared Memory Key for Deployment status
DEPLOYMENT_STATUS_SUMMARY_KEY_TEMPLATE = "deployment:{request_id}:status_summary" # Using request_id
DEPLOYMENT_STATUS_EXPIRY_SECONDS = int(os.getenv("CICD_AGENT_DEPLOYMENT_STATUS_EXPIRY_SECONDS", 7 * 24 * 60 * 60))


class CICDAgent:
    def __init__(self):
        logger.info(f"Initializing CI_CD_Agent (V0.3 with SharedMemory status persistence)...")
        self.event_bus = EventBus()
        self.shared_memory = SharedMemoryStore() # <<< INITIALIZE SharedMemoryStore
        self.http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {RAILWAY_API_TOKEN}", "Content-Type": "application/json"},
            timeout=60.0
        )
        # ... (checks for critical env vars as in V0.3 / response #79) ...
        if not self.event_bus.redis_client: logger.error("CI_CD_Agent: EventBus not connected.")
        if not self.shared_memory.redis_client: logger.warning("CI_CD_Agent: SharedMemoryStore not connected. Will not persist final deployment status for API.")
        if not RAILWAY_API_TOKEN: logger.error("CI_CD_Agent critical: RAILWAY_API_TOKEN not set.")
        if not RAILWAY_PROJECT_ID: logger.error("CI_CD_Agent critical: RAILWAY_PROJECT_ID not set.")
        logger.info("CI_CD_Agent V0.3 Initialized.")

    @property
    def tracer_instance(self): return _tracer
    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same helper as before) ...
        if self.tracer_instance and _trace_api:
            span = self.tracer_instance.start_span(f"cicd_agent.{operation_name}", context=parent_context)
            for k, v in attrs.items(): span.set_attribute(k, v)
            return span
        class NoOpSpan:
            def __enter__(self): return self; def __exit__(self,tp,vl,tb): pass; def set_attribute(self,k,v): pass; 
            def record_exception(self,e,attributes=None): pass; def set_status(self,s): pass; def end(self): pass
        return NoOpSpan()


    async def _store_final_deployment_status_in_shared_memory(self, request_id: str, status_event_data: DeploymentStatusEvent):
        """Stores the final deployment status summary in SharedMemoryStore."""
        if not self.shared_memory.redis_client:
            logger.warning(f"Deployment Request {request_id}: Cannot store final status, SharedMemoryStore not connected.")
            return

        status_key = DEPLOYMENT_STATUS_SUMMARY_KEY_TEMPLATE.format(request_id=request_id)
        # status_event_data is already a TypedDict, SharedMemoryStore will serialize it.
        # This data should match SDKDeploymentStatusModel for ForgeIQ-backend to serve it.
        try:
            success = await self.shared_memory.set_value(
                status_key,
                status_event_data, # This is a dict
                expiry_seconds=DEPLOYMENT_STATUS_EXPIRY_SECONDS
            )
            if success:
                logger.info(f"Deployment Request {request_id}: Final status summary (Status: {status_event_data['status']}) stored to SharedMemory: '{status_key}'.")
            else:
                logger.error(f"Deployment Request {request_id}: Failed to store final status summary to SharedMemory for key '{status_key}'.")
        except Exception as e:
            logger.error(f"Deployment Request {request_id}: Exception storing final status to SharedMemory: {e}", exc_info=True)


    def _publish_deployment_status(self, internal_deployment_id: str, request_event: DeploymentRequestEvent, 
                                   status: str, message: Optional[str] = None, 
                                   deployment_url: Optional[str] = None, logs_url: Optional[str] = None,
                                   started_at: Optional[str] = None, completed_at: Optional[str] = None,
                                   railway_specific_deployment_id: Optional[str] = None):
        # (Construct status_event_payload as in response #79)
        status_event_payload: Dict[str, Any] = {
            "event_type": "DeploymentStatusEvent",
            "deployment_id": railway_specific_deployment_id or internal_deployment_id,
            "request_id": request_event["request_id"],
            "project_id": request_event["project_id"],
            "service_name": request_event["service_name"],
            "commit_sha": request_event["commit_sha"],
            "target_environment": request_event["target_environment"],
            "status": status,
            "timestamp": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        }
        if message is not None: status_event_payload["message"] = message # Important: allow empty string for message
        if deployment_url: status_event_payload["deployment_url"] = deployment_url
        if logs_url: status_event_payload["logs_url"] = logs_url
        if started_at: status_event_payload["started_at"] = started_at
        if completed_at: status_event_payload["completed_at"] = completed_at

        status_event_typed = DeploymentStatusEvent(**status_event_payload) #type: ignore

        if self.event_bus.redis_client:
            channel = DEPLOYMENT_STATUS_CHANNEL_TEMPLATE.format(
                project_id=request_event["project_id"], service_name=request_event["service_name"]
            )
            self.event_bus.publish(channel, status_event_typed) #type: ignore
            logger.info(f"Published DeploymentStatusEvent for internal_id {internal_deployment_id} / railway_id {railway_specific_deployment_id or 'N/A'} ({request_event['service_name']}): {status}")

        # If it's a terminal status, also store it in SharedMemory
        is_terminal_status = status.upper() in ["SUCCESSFUL", "FAILED", "API_ERROR", "POLL_ERROR", "UNKNOWN_TERMINAL"]
        if is_terminal_status:
            logger.info(f"Deployment Request {request_event['request_id']} reached terminal state '{status}'. Scheduling storage of final status.")
            asyncio.create_task(self._store_final_deployment_status_in_shared_memory(request_event["request_id"], status_event_typed))


    # ... _trigger_railway_deployment_cli, poll_railway_deployment_status, execute_deployment_flow,
    #     handle_deployment_request_event, main_event_loop ...
    # These methods remain the same as the V0.3 detailed in response #79, which already included
    # the logic for CLI triggering and API polling.
    # The key change is that _publish_deployment_status now also triggers state storage.
    # I'll re-paste them here for completeness and ensure all imports are fine.

    async def _trigger_railway_deployment_cli(self, request_event: DeploymentRequestEvent, internal_deployment_id: str) -> Optional[str]:
        service_name = request_event["service_name"]
        command = ["railway", "deploy", "--service", service_name, "--detach"]
        logger.info(f"Deployment {internal_deployment_id}: Triggering Railway CLI for '{service_name}'. Cmd: {' '.join(command)}")
        env = os.environ.copy(); 
        if RAILWAY_API_TOKEN: env["RAILWAY_TOKEN"] = RAILWAY_API_TOKEN
        if RAILWAY_PROJECT_ID: env["RAILWAY_PROJECT_ID"] = RAILWAY_PROJECT_ID
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        stdout, stderr = await process.communicate(); stdout_str = stdout.decode('utf-8').strip() if stdout else ""; stderr_str = stderr.decode('utf-8').strip() if stderr else ""
        if process.returncode == 0:
            logger.info(f"Deployment {internal_deployment_id}: CLI trigger for '{service_name}' successful. Output: {stdout_str}")
            match = re.search(r"(?:Deployment ID|deployments/|Deployment triggered:)\s*([a-f0-9\-]+)", stdout_str, re.IGNORECASE)
            if match: railway_depl_id = match.group(1); logger.info(f"Extracted Railway Deployment ID: {railway_depl_id}"); return railway_depl_id
            logger.warning(f"Could not extract Railway deployment ID from CLI output: {stdout_str}"); return "cli_trigger_successful_no_specific_id"
        else: logger.error(f"Deployment {internal_deployment_id}: CLI trigger for '{service_name}' failed. RC: {process.returncode}. Err: {stderr_str}.
