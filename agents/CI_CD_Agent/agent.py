# ======================================
# 📁 agents/CI_CD_Agent/agent.py (V0.3)
# ======================================
import os
import json
import datetime
import uuid
import asyncio
import logging
import subprocess # For Railway CLI
import httpx      # For Railway API calls
import time       # For monotonic time in polling
from typing import Dict, Any, Optional, List

# --- Observability Setup ---
SERVICE_NAME = "CI_CD_Agent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(otelTraceID)s - %(otelSpanID)s - %(message)s'
)
_tracer = None
_trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing # Assuming this path is correct
    _tracer = setup_tracing(SERVICE_NAME)
    _trace_api = otel_trace_api
except ImportError:
    logging.getLogger(SERVICE_NAME).warning("CI_CD_Agent: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from interfaces.types.events import DeploymentRequestEvent, DeploymentStatusEvent

# Railway Configuration
RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN")
RAILWAY_PROJECT_ID = os.getenv("RAILWAY_PROJECT_ID")
RAILWAY_API_BASE_URL = os.getenv("RAILWAY_API_BASE_URL", "https://api.railway.app") 

# Polling Configuration
DEPLOYMENT_POLL_INTERVAL_SECONDS = int(os.getenv("DEPLOYMENT_POLL_INTERVAL_SECONDS", "30"))
DEPLOYMENT_POLL_TIMEOUT_SECONDS = int(os.getenv("DEPLOYMENT_POLL_TIMEOUT_SECONDS", "1800")) # 30 minutes

# Event channels
DEPLOYMENT_REQUEST_CHANNEL_PATTERN = "events.project.*.deployment.request"
DEPLOYMENT_STATUS_CHANNEL_TEMPLATE = "events.project.{project_id}.service.{service_name}.deployment.status"

class CICDAgent:
    def __init__(self):
        logger.info(f"Initializing CI_CD_Agent (V0.3 with API Polling) for Project ID: {RAILWAY_PROJECT_ID or 'Not Set'}")
        self.event_bus = EventBus()

        if not RAILWAY_API_TOKEN:
            logger.error("CI_CD_Agent critical: RAILWAY_API_TOKEN not set. API operations will fail.")
            # self.http_client will not be initialized properly, but let constructor finish
        self.http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {RAILWAY_API_TOKEN}", "Content-Type": "application/json"},
            timeout=60.0 # General timeout for API calls
        )

        if not self.event_bus.redis_client:
            logger.error("CI_CD_Agent critical: EventBus not connected.")
        if not RAILWAY_PROJECT_ID: # Project ID is crucial for most Railway API calls
            logger.error("CI_CD_Agent critical: RAILWAY_PROJECT_ID not set. API operations will likely fail.")

        logger.info("CI_CD_Agent V0.3 Initialized.")

    @property
    def tracer_instance(self):
        return _tracer

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same helper as before) ...
        if self.tracer_instance and _trace_api:
            span = self.tracer_instance.start_span(f"cicd_agent.{operation_name}", context=parent_context)
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

    def _publish_deployment_status(self, internal_deployment_id: str, request_event: DeploymentRequestEvent, 
                                   status: str, message: Optional[str] = None, 
                                   deployment_url: Optional[str] = None, logs_url: Optional[str] = None,
                                   started_at: Optional[str] = None, completed_at: Optional[str] = None,
                                   railway_specific_deployment_id: Optional[str] = None): # Added railway_specific_deployment_id
        if self.event_bus.redis_client:
            # Use the request_id from the original request for external correlation
            # Use internal_deployment_id for our specific attempt tracking.
            # The DeploymentStatusEvent's "deployment_id" field can be our internal_deployment_id or railway_specific_id

            status_event_payload = { # Building dict first for easier optional key handling
                "event_type": "DeploymentStatusEvent",
                "deployment_id": railway_specific_deployment_id or internal_deployment_id, # Prefer Railway's ID if available
                "request_id": request_event["request_id"],
                "project_id": request_event["project_id"],
                "service_name": request_event["service_name"],
                "commit_sha": request_event["commit_sha"],
                "target_environment": request_event["target_environment"],
                "status": status,
                "timestamp": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            }
            if message: status_event_payload["message"] = message
            if deployment_url: status_event_payload["deployment_url"] = deployment_url
            if logs_url: status_event_payload["logs_url"] = logs_url
            if started_at: status_event_payload["started_at"] = started_at
            if completed_at: status_event_payload["completed_at"] = completed_at

            status_event = DeploymentStatusEvent(**status_event_payload) #type: ignore

            channel = DEPLOYMENT_STATUS_CHANNEL_TEMPLATE.format(
                project_id=request_event["project_id"], 
                service_name=request_event["service_name"]
            )
            self.event_bus.publish(channel, status_event) #type: ignore
            logger.info(f"Published DeploymentStatusEvent for internal_id {internal_deployment_id} / railway_id {railway_specific_deployment_id or 'N/A'} ({request_event['service_name']}): {status}")

            # Persist final status to SharedMemoryStore
            is_terminal_status = status in ["SUCCESSFUL", "FAILED", "API_ERROR", "POLL_ERROR", "UNKNOWN_TERMINAL"]
            if is_terminal_status:
                # Ensure shared_memory is available (it should be if class init was successful)
                # We need to instantiate it or have it passed in if it's not a global
                # For now, let's assume it's available via a self.shared_memory instance
                # This part was missing in the previous CI_CD_Agent definition.
                # Let's add shared_memory to __init__ if we decide to use it.
                # For now, this is conceptual for publishing the event. Persisting is next.
                pass # See _store_final_deployment_status for actual persistence logic

    async def _store_final_deployment_status(self, request_id: str, status_event_data: DeploymentStatusEvent):
        """Conceptual: Stores the final deployment status summary in SharedMemoryStore."""
        # This was defined in response #77 thought process, should be implemented here.
        # from core.shared_memory import SharedMemoryStore # Should be instance variable
        # shared_memory = SharedMemoryStore() # Or self.shared_memory
        # if not shared_memory.redis_client:
        #     logger.warning(f"Deployment Request {request_id}: Cannot store final status, SharedMemoryStore not connected.")
        #     return
        # status_key = f"deployment:{request_id}:status_summary" # Using request_id from original event
        # success = await shared_memory.set_value(status_key, status_event_data, expiry_seconds=7*24*60*60)
        # if success: logger.info(f"Deployment Request {request_id}: Final status summary stored to SharedMemory.")
        # else: logger.error(f"Deployment Request {request_id}: Failed to store final status summary.")
        logger.debug(f"Conceptual: Store final status for request_id {request_id}: {status_event_data['status']}")


    async def _trigger_railway_deployment_cli(self, request_event: DeploymentRequestEvent, internal_deployment_id: str) -> Optional[str]:
        """Triggers a Railway deployment using CLI and attempts to get a Railway deployment ID."""
        # (Logic from response #77 - _trigger_railway_deployment_cli, ensuring it uses async subprocess)
        service_name = request_event["service_name"]
        command = ["railway", "deploy", "--service", service_name, "--detach"]
        # if request_event["target_environment"]: # If Railway CLI supports --environment for `deploy`
        #    command.extend(["--environment", request_event["target_environment"]])

        logger.info(f"Deployment {internal_deployment_id}: Triggering Railway CLI for service '{service_name}'. Command: {' '.join(command)}")
        env = os.environ.copy()
        if RAILWAY_API_TOKEN: env["RAILWAY_TOKEN"] = RAILWAY_API_TOKEN
        if RAILWAY_PROJECT_ID: env["RAILWAY_PROJECT_ID"] = RAILWAY_PROJECT_ID

        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
        )
        stdout, stderr = await process.communicate()
        stdout_str = stdout.decode('utf-8').strip() if stdout else ""; stderr_str = stderr.decode('utf-8').strip() if stderr else ""

        if process.returncode == 0:
            logger.info(f"Deployment {internal_deployment_id}: CLI trigger for '{service_name}' successful. Output: {stdout_str}")
            # Try to parse Railway's actual deployment ID (this is highly dependent on CLI output format)
            # Example: "Deployment ID: 部署_id_ ഇവിടെ" or "View deployment at https://railway.app/.../deployments/部署_id_ ഇവിടെ"
            match = re.search(r"(?:Deployment ID|deployments/|Deployment triggered:)\s*([a-f0-9\-]+)", stdout_str, re.IGNORECASE)
            if match:
                railway_depl_id = match.group(1)
                logger.info(f"Extracted Railway Deployment ID: {railway_depl_id} for service {service_name}")
                return railway_depl_id
            logger.warning(f"Could not extract specific Railway deployment ID from CLI output for '{service_name}'. Polling specific ID may not work. Output: {stdout_str}")
            return "cli_trigger_successful_no_specific_id" # Indicates trigger worked but no ID for API polling
        else:
            logger.error(f"Deployment {internal_deployment_id}: CLI trigger for '{service_name}' failed. RC: {process.returncode}. Err: {stderr_str}. Out: {stdout_str}")
            return None

    async def poll_railway_deployment_status(self, railway_deployment_id: str, project_id_on_railway: str, service_id_on_railway: str) -> Dict[str, Any]:
        """Polls Railway API for the status of a specific deployment ID."""
        # (Logic from response #77, ensuring it uses self.http_client and RAILWAY_PROJECT_ID for the project context)
        # Note: Railway's API might use project ID + service ID + deployment ID, or just deployment ID if global.
        # Assuming /v2/projects/{project_id}/deployments/{deployment_id} or /v2/deployments/{deployment_id}

        if not RAILWAY_PROJECT_ID: # Ensure we use the class/env RAILWAY_PROJECT_ID
            logger.error("RAILWAY_PROJECT_ID not configured for API polling.")
            return {"status": "POLL_ERROR", "message": "RAILWAY_PROJECT_ID not configured."}

        # Construct URL carefully. The `railway_deployment_id` from CLI might be the one to use directly.
        # Or, you might list deployments for a service and find the latest.
        # For polling a specific ID:
        api_url = f"{RAILWAY_API_BASE_URL}/v2/deployments/{railway_deployment_id}"
        # Alternative if project ID is needed in path:
        # api_url = f"{RAILWAY_API_BASE_URL}/v2/projects/{RAILWAY_PROJECT_ID}/deployments/{railway_deployment_id}"

        logger.debug(f"Polling Railway API for deployment {railway_deployment_id} at {api_url}")
        try:
            response = await self.http_client.get(api_url) # self.http_client has auth headers
            response.raise_for_status()
            data = response.json() # This is the raw deployment object from Railway API
            logger.debug(f"Railway API raw response for deployment {railway_deployment_id}: {message_summary(data, 300)}")

            railway_status = data.get("status", "UNKNOWN").upper()
            # Extract canonical URL and logs URL if available from Railway's deployment object
            # This depends heavily on Railway's actual API response structure.
            # Example: data.get("domains", [{}])[0].get("name") for public URL
            # Example: data.get("service", {}).get("id") might give serviceId for constructing logs URL
            # Railway specific service ID might be different from what we call "service_name"
            service_name_from_event = service_id_on_railway # Use the name we have
            deployment_url_from_api = data.get("url") # This is a guess
            # Construct a plausible logs URL for Railway
            logs_url_railway = f"https://railway.app/project/{RAILWAY_PROJECT_ID}/service/{service_name_from_event}/deployments/{railway_deployment_id}"


            final_status = "UNKNOWN"; message = f"Railway status: {railway_status}"
            if railway_status in ["SUCCESS", "ACTIVE", "DEPLOYED", "COMPLETED", "DONE"]: # Common success states
                final_status = "SUCCESSFUL"
            elif railway_status in ["FAILED", "CRASHED", "ERROR", "REMOVED", "THROTTLED"]: # Common failure states
                final_status = "FAILED"
                message = data.get("meta", {}).get("error", {}).get("message", f"Railway deployment failed: {railway_status}")
            elif railway_status in ["BUILDING", "DEPLOYING", "INIT", "QUEUED", "PENDING", "PREPARING", "INITIALIZING", "WAITING"]:
                final_status = "IN_PROGRESS"
            else: final_status = "UNKNOWN_TERMINAL"; message = f"Unhandled Railway status: {railway_status}"

            return {"status": final_status, "message": message, "deployment_url": deployment_url_from_api, "logs_url": logs_url_railway, "raw_railway_status": railway_status}
        except httpx.HTTPStatusError as e:
            logger.error(f"Railway API HTTP error polling {railway_deployment_id}: {e.response.status_code} - {e.response.text[:200]}")
            return {"status": "API_ERROR", "message": f"Railway API error: {e.response.status_code}"}
        except Exception as e:
            logger.error(f"Error polling Railway deployment {railway_deployment_id}: {e}", exc_info=True)
            return {"status": "POLL_ERROR", "message": f"Polling error: {e}"}

    async def execute_deployment_flow(self, request_event: DeploymentRequestEvent): # Renamed from execute_deployment_with_polling
        internal_deployment_id = str(uuid.uuid4())
        started_at_iso = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        span_name = f"execute_deployment_flow:{request_event['service_name']}"
        parent_context = None # Extract from request_event if it carries trace context
        span = self._start_trace_span_if_available(span_name, parent_context=parent_context, **request_event) # type: ignore

        try:
            with span: #type: ignore
                self._publish_deployment_status(internal_deployment_id, request_event, "STARTED", started_at=started_at_iso, message="Deployment process initiated.")
                if not RAILWAY_API_TOKEN or not RAILWAY_PROJECT_ID: # Moved check here
                    msg = "RAILWAY_API_TOKEN or RAILWAY_PROJECT_ID not configured."; logger.error(msg)
                    self._publish_deployment_status(internal_deployment_id, request_event, "FAILED", started_at=started_at_iso, message=msg)
                    if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, msg)); 
                    return

                railway_deployment_id_from_cli = await self._trigger_railway_deployment_cli(request_event, internal_deployment_id)

                if not railway_deployment_id_from_cli:
                    msg = "Failed to trigger Railway deployment via CLI."
                    self._publish_deployment_status(internal_deployment_id, request_event, "FAILED", started_at=started_at_iso, message=msg)
                    if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, msg))
                    return

                if railway_deployment_id_from_cli == "cli_trigger_successful_no_specific_id":
                    msg = "Railway deployment trigger succeeded via CLI, but specific Railway deployment ID could not be extracted for status polling. Monitor Railway dashboard directly."
                    logger.warning(msg)
                    self._publish_deployment_status(internal_deployment_id, request_event, "SUCCESSFUL", # Successful trigger
                                                   message=msg, started_at=started_at_iso, 
                                                   completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z")
                    if _trace_api and span: span.set_attribute("cicd.warning", "no_railway_id_for_polling"); span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                    return

                # Proceed with polling using the actual railway_deployment_id_from_cli
                self._publish_deployment_status(internal_deployment_id, request_event, "IN_PROGRESS", started_at=started_at_iso, 
                                               message=f"Deployment triggered on Railway (ID: {railway_deployment_id_from_cli}). Polling status...",
                                               railway_specific_deployment_id=railway_deployment_id_from_cli)

                polling_start_time = time.monotonic()
                final_poll_result = {"status": "UNKNOWN_TERMINAL", "message": "Polling did not determine final status."}

                while True:
                    if time.monotonic() - polling_start_time > DEPLOYMENT_POLL_TIMEOUT_SECONDS:
                        final_poll_result = {"status": "FAILED", "message": f"Polling timed out after {DEPLOYMENT_POLL_TIMEOUT_SECONDS}s."}
                        logger.error(f"Deployment {internal_deployment_id}: {final_poll_result['message']}")
                        if _trace_api and span: span.set_attribute("cicd.polling_timeout", True)
                        break

                    # Pass project_id and service_name from the original request event for context in poll_railway_deployment_status
                    poll_result = await self.poll_railway_deployment_status(
                        railway_deployment_id_from_cli, 
                        request_event["project_id"], # This is the Railway Project ID
                        request_event["service_name"] # This is the Railway Service Name/ID
                    )
                    current_poll_status = poll_result.get("status", "UNKNOWN")

                    if _trace_api and span: span.add_event("PollRailwayStatus", {"railway_status": poll_result.get("raw_railway_status"), "mapped_status": current_poll_status})

                    if current_poll_status == "SUCCESSFUL":
                        final_poll_result = poll_result; break
                    elif current_poll_status in ["FAILED", "API_ERROR", "POLL_ERROR", "UNKNOWN_TERMINAL"]:
                        final_poll_result = poll_result; break
                    elif current_poll_status == "IN_PROGRESS":
                        logger.info(f"Deployment {internal_deployment_id} (Railway ID {railway_deployment_id_from_cli}): Still IN_PROGRESS (Raw: {poll_result.get('raw_railway_status')}). Waiting...")
                        self._publish_deployment_status(internal_deployment_id, request_event, "IN_PROGRESS", 
                                                       message=f"Railway status: {poll_result.get('raw_railway_status')}", 
                                                       started_at=started_at_iso, logs_url=poll_result.get("logs_url"),
                                                       railway_specific_deployment_id=railway_deployment_id_from_cli)
                    else: # UNKNOWN, etc.
                        logger.warning(f"Deployment {internal_deployment_id}: Received unhandled poll status '{current_poll_status}'. Retrying.")

                    await asyncio.sleep(DEPLOYMENT_POLL_INTERVAL_SECONDS)

                self._publish_deployment_status(
                    internal_deployment_id, request_event,
                    final_poll_result["status"], message=final_poll_result.get("message"),
                    deployment_url=final_poll_result.get("deployment_url"), logs_url=final_poll_result.get("logs_url"),
                    started_at=started_at_iso, completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
                    railway_specific_deployment_id=railway_deployment_id_from_cli
                )
                if _trace_api and span:
                    if final_poll_result["status"] == "SUCCESSFUL": span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                    else: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, final_poll_result.get("message", "Deployment ended unsuccessfully")))
        except Exception as e_flow:
            logger.error(f"Unhandled error in deployment flow {internal_deployment_id}: {e_flow}", exc_info=True)
            self._publish_deployment_status(internal_deployment_id, request_event, "FAILED", message=f"Agent error: {str(e_flow)}", started_at=started_at_iso)
            if _trace_api and span: span.record
