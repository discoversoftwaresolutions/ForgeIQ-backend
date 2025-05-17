# ======================================
# 📁 agents/CI_CD_Agent/app/agent.py
# ======================================
import os
import json
import datetime
import uuid
import asyncio
import logging
import subprocess
# import httpx # httpx was for API polling, if still used
from typing import Dict, Any, Optional, List


# --- Observability Setup (as before) ---
SERVICE_NAME = "CI_CD_Agent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s')
tracer = None
try:
    from opentelemetry import trace
    from core.observability.tracing import setup_tracing
    tracer = setup_tracing(SERVICE_NAME)
except ImportError:
    logging.getLogger(SERVICE_NAME).warning("CI_CD_Agent: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from core.shared_memory import SharedMemoryStore # <<< IMPORT SharedMemoryStore
from interfaces.types.events import DeploymentRequestEvent, DeploymentStatusEvent

RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN")
RAILWAY_PROJECT_ID = os.getenv("RAILWAY_PROJECT_ID") 
# RAILWAY_API_BASE_URL = os.getenv("RAILWAY_API_BASE_URL", "https://api.railway.app") # For polling
# DEPLOYMENT_POLL_INTERVAL_SECONDS = int(os.getenv("DEPLOYMENT_POLL_INTERVAL_SECONDS", "30"))
# DEPLOYMENT_POLL_TIMEOUT_SECONDS = int(os.getenv("DEPLOYMENT_POLL_TIMEOUT_SECONDS", "1800"))


# Event channels (as before)
DEPLOYMENT_REQUEST_CHANNEL_PATTERN = "events.project.*.deployment.request"
DEPLOYMENT_STATUS_CHANNEL_TEMPLATE = "events.project.{project_id}.service.{service_name}.deployment.status"

# Shared Memory Key for Deployment status
DEPLOYMENT_STATUS_SUMMARY_KEY_TEMPLATE = "deployment:{request_id}:status_summary" # Key ForgeIQ-backend will query


class CICDAgent:
    def __init__(self):
        logger.info("Initializing CI_CD_Agent (V0.2 with SharedMemory for status)...")
        self.event_bus = EventBus()
        self.shared_memory = SharedMemoryStore() # <<< INITIALIZE SharedMemoryStore
        # self.http_client = httpx.AsyncClient(...) # If polling Railway API
        if not self.event_bus.redis_client:
            logger.error("CI_CD_Agent: EventBus not connected.")
        if not self.shared_memory.redis_client:
            logger.warning("CI_CD_Agent: SharedMemoryStore not connected. Will not persist final deployment status.")
        if not RAILWAY_API_TOKEN:
            logger.warning("RAILWAY_API_TOKEN not set. Railway CLI operations may fail.")
        logger.info("CI_CD_Agent V0.2 Initialized.")

    @property
    def tracer_instance(self):
        return tracer

    async def _store_final_deployment_status(self, request_id: str, status_event_data: DeploymentStatusEvent):
        """Stores the final deployment status summary in SharedMemoryStore."""
        if not self.shared_memory.redis_client:
            logger.warning(f"Deployment Request {request_id}: Cannot store final status, SharedMemoryStore not connected.")
            return

        status_key = DEPLOYMENT_STATUS_SUMMARY_KEY_TEMPLATE.format(request_id=request_id)
        try:
            success = await self.shared_memory.set_value(
                status_key,
                status_event_data, # This is a dict
                expiry_seconds=7 * 24 * 60 * 60 # Store for 7 days
            )
            if success:
                logger.info(f"Deployment Request {request_id}: Final status summary stored to SharedMemory key '{status_key}'.")
            else:
                logger.error(f"Deployment Request {request_id}: Failed to store final status summary to SharedMemory for key '{status_key}'.")
        except Exception as e:
            logger.error(f"Deployment Request {request_id}: Exception storing final status to SharedMemory: {e}", exc_info=True)

    def _publish_deployment_status(self, deployment_id_internal: str, request_event: DeploymentRequestEvent, 
                                   status: str, message: Optional[str] = None, 
                                   deployment_url: Optional[str] = None, logs_url: Optional[str] = None,
                                   started_at: Optional[str] = None, completed_at: Optional[str] = None):
        # ... (event construction as before from response #61) ...
        status_event_data = DeploymentStatusEvent(
            event_type="DeploymentStatusEvent", deployment_id=deployment_id_internal,
            request_id=request_event["request_id"], project_id=request_event["project_id"],
            service_name=request_event["service_name"], commit_sha=request_event["commit_sha"],
            target_environment=request_event["target_environment"], status=status, message=message,
            deployment_url=deployment_url, logs_url=logs_url,
            started_at=started_at, completed_at=completed_at,
            timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        )
        
        if self.event_bus.redis_client:
            channel = DEPLOYMENT_STATUS_CHANNEL_TEMPLATE.format(
                project_id=request_event["project_id"], service_name=request_event["service_name"]
            )
            self.event_bus.publish(channel, status_event_data)
            logger.info(f"Published DeploymentStatusEvent for {deployment_id_internal} ({request_event['service_name']}): {status}")

        # If it's a terminal status, also store it in SharedMemory
        is_terminal_status = status in ["SUCCESSFUL", "FAILED", "API_ERROR", "POLL_ERROR"] # Add any other terminal states
        if is_terminal_status:
            asyncio.create_task(self._store_final_deployment_status(request_event["request_id"], status_event_data))
            
    # ... (rest of CICDAgent class: _trigger_railway_deployment_cli, poll_railway_deployment_status (if used),
    #      execute_deployment_with_polling (or simpler execute_deployment_trigger), 
    #      handle_deployment_request_event, main_event_loop as defined in response #47 or #61) ...
    # I'll re-paste the structure from #47 (simpler trigger, no API polling) and integrate state storage.
    # If you prefer the polling version from #61, let me know, it's more complex.

    async def _run_railway_command(self, command: List[str], deployment_id_internal: str) -> Dict[str, Any]:
        logger.info(f"Deployment {deployment_id_internal}: Running Railway command: {' '.join(command)}")
        env = os.environ.copy(); 
        if RAILWAY_API_TOKEN: env["RAILWAY_TOKEN"] = RAILWAY_API_TOKEN
        if RAILWAY_PROJECT_ID: env["RAILWAY_PROJECT_ID"] = RAILWAY_PROJECT_ID
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        stdout, stderr = await process.communicate()
        stdout_str = stdout.decode().strip() if stdout else ""; stderr_str = stderr.decode().strip() if stderr else ""
        if process.returncode == 0:
            logger.info(f"Deployment {deployment_id_internal}: Railway CLI trigger successful. Output: {stdout_str[:200]}")
            # Try to find a Railway deployment ID in stdout for future reference, though not strictly used in this version
            railway_depl_id_from_cli = "N/A" 
            # Example parsing (very basic, depends on actual CLI output)
            if "Deployment ID:" in stdout_str: railway_depl_id_from_cli = stdout_str.split("Deployment ID:")[-1].strip().split()[0]
            elif "ID " in stdout_str: railway_depl_id_from_cli = stdout_str.split("ID ")[-1].strip().split()[0]

            return {"success": True, "stdout": stdout_str, "stderr": stderr_str, "railway_deployment_id": railway_depl_id_from_cli}
        else:
            logger.error(f"Deployment {deployment_id_internal}: Railway CLI trigger failed. Code: {process.returncode}. Error: {stderr_str}. Output: {stdout_str[:200]}")
            return {"success": False, "stdout": stdout_str, "stderr": stderr_str, "exit_code": process.returncode}

    async def execute_deployment_trigger(self, request_event: DeploymentRequestEvent):
        """Triggers deployment and publishes initial status. Does not poll."""
        deployment_id_internal = str(uuid.uuid4()) # Our internal tracking ID for this attempt
        started_at_iso = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        span_name = f"cicd_agent.execute_deployment_trigger:{request_event['service_name']}"
        parent_span_context = None # Extract if in request_event for full trace
        
        if not self.tracer_instance: # Fallback
             await self._execute_deployment_trigger_logic(deployment_id_internal, request_event, started_at_iso)
             return

        with self.tracer_instance.start_as_current_span(span_name, context=parent_span_context) as span:
            span.set_attributes({
                "cicd.deployment_id_internal": deployment_id_internal, "cicd.request_id": request_event["request_id"],
                "cicd.project_id": request_event["project_id"], "cicd.service_name": request_event["service_name"],
                "cicd.commit_sha": request_event["commit_sha"], "cicd.target_environment": request_event["target_environment"]
            })
            logger.info(f"Starting deployment trigger {deployment_id_internal} for service '{request_event['service_name']}'")
            await self._execute_deployment_trigger_logic(deployment_id_internal, request_event, started_at_iso)


    async def _execute_deployment_trigger_logic(self, deployment_id_internal: str, request_event: DeploymentRequestEvent, started_at_iso: str):
        self._publish_deployment_status(deployment_id_internal, request_event, "STARTED", started_at=started_at_iso, message="Deployment trigger initiated.")

        if not RAILWAY_API_TOKEN: # RAILWAY_PROJECT_ID might also be checked if CLI needs it explicitly
            msg = "RAILWAY_API_TOKEN not configured. Cannot trigger deployment."
            logger.error(f"Deployment {deployment_id_internal}: {msg}")
            self._publish_deployment_status(deployment_id_internal, request_event, "FAILED", started_at=started_at_iso, message=msg)
            if self.tracer_instance and trace_api: trace.get_current_span().set_status(trace_api.Status(trace_api.StatusCode.ERROR, msg))
            return

        # Command for Railway CLI - `railway up` is often used for general "deploy latest from linked branch/config"
        # `railway deploy` might offer more specific targeting. Let's use `up --detach`.
        # Ensure the service name matches what's in Railway.
        command = ["railway", "up", "--service", request_event["service_name"], "--detach"]
        # If Railway environments are distinct entities targeted by CLI:
        # command.extend(["--environment", request_event["target_environment"]])

        cli_result = await self._run_railway_command(command, deployment_id_internal)

        if cli_result["success"]:
            msg = f"Railway deployment successfully triggered for service '{request_event['service_name']}'. Railway Deployment ID (if found): {cli_result.get('railway_deployment_id', 'N/A')}. Monitor Railway for actual deployment status."
            logger.info(f"Deployment {deployment_id_internal}: {msg}")
            self._publish_deployment_status(
                deployment_id_internal, request_event, "SUCCESSFUL", # This means trigger was successful
                message=msg, started_at=started_at_iso,
                completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
                # deployment_url can be fetched later or is known by Railway service config
                # logs_url can be constructed if railway_deployment_id is reliably parsed
            )
            if self.tracer_instance and trace_api: trace.get_current_span().set_status(trace_api.Status(trace_api.StatusCode.OK))
        else:
            error_message = f"Railway deployment trigger failed. Exit: {cli_result.get('exit_code','N/A')}. Err: {cli_result['stderr']}. Out: {cli_result['stdout']}"
            logger.error(f"Deployment {deployment_id_internal}: {error_message}")
            self._publish_deployment_status(
                deployment_id_internal, request_event, "FAILED", message=error_message,
                started_at=started_at_iso,
                completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            )
            if self.tracer_instance and trace_api: trace.get_current_span().set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Railway CLI trigger failed"))

    async def handle_deployment_request_event(self, event_data_str: str):
        # ... (logic from response #61, calls execute_deployment_trigger via asyncio.create_task) ...
        span_name = "cicd_agent.handle_deployment_request_event"; parent_context = None
        if not self.tracer_instance: await self._handle_deployment_request_event_logic(event_data_str); return
        with self.tracer_instance.start_as_current_span(span_name, context=parent_context) as span:
            try:
                request: DeploymentRequestEvent = json.loads(event_data_str) #type: ignore
                if not (request.get("event_type") == "DeploymentRequestEvent" and all(k in request for k in ["request_id", "project_id", "service_name", "commit_sha", "target_environment"])):
                    logger.error(f"Malformed DeploymentRequestEvent: {event_data_str[:200]}"); span.set_status(trace.Status(trace.StatusCode.ERROR, "Malformed event")); return
                span.set_attributes({"messaging.system": "redis", "cicd.request_id": request["request_id"]})
                logger.info(f"CI_CD_Agent handling DeploymentRequestEvent: {request['request_id']} for service {request['service_name']}")
                asyncio.create_task(self.execute_deployment_trigger(request)) # Using simpler trigger for V0.2 enhancement
            except json.JSONDecodeError: logger.error(f"Could not decode JSON: {event_data_str[:200]}"); span.set_status(trace.Status(trace.StatusCode.ERROR, "JSON decode error"))
            except Exception as e: logger.error(f"Error handling DeploymentRequestEvent: {e}", exc_info=True); span.record_exception(e); span.set_status(trace.Status(trace.StatusCode.ERROR, "Event handling failed"))

    async def main_event_loop(self):
        # ... (logic from response #61, subscribes to DEPLOYMENT_REQUEST_CHANNEL_PATTERN) ...
        if not self.event_bus.redis_client: logger.critical("CI_CD_Agent: EventBus not connected. Exiting."); await asyncio.sleep(60); return
        pubsub = self.event_bus.subscribe_to_channel(DEPLOYMENT_REQUEST_CHANNEL_PATTERN)
        if not pubsub: logger.critical(f"CI_CD_Agent: Failed to subscribe. Exiting."); await asyncio.sleep(60); return
        logger.info(f"CI_CD_Agent worker subscribed to '{DEPLOYMENT_REQUEST_CHANNEL_PATTERN}', listening...")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "pmessage":
                    await self.handle_deployment_request_event(message["data"])
                await asyncio.sleep(0.01)
        except KeyboardInterrupt: logger.info("CI_CD_Agent event loop interrupted.")
        except Exception as e: logger.error(f"Critical error in CI_CD_Agent event loop: {e}", exc_info=True)
        finally:
            logger.info("CI_CD_Agent shutting down pubsub...");
            if pubsub:
                try: 
                    await asyncio.to_thread(pubsub.punsubscribe, DEPLOYMENT_REQUEST_CHANNEL_PATTERN)
                    await asyncio.to_thread(pubsub.close)
                except Exception as e_close: logger.error(f"Error closing pubsub for CI_CD_Agent: {e_close}")
            logger.info("CI_CD_Agent shutdown complete.")


async def main_async_runner():
    agent = CICDAgent()
    await agent.main_event_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main_async_runner())
    except KeyboardInterrupt:
        logger.info("CI_CD_Agent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"CI_CD_Agent failed to start or unhandled error: {e}", exc_info=True)
