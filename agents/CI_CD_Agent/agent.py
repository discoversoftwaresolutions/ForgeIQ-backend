# ======================================
# 📁 agents/CI_CD_Agent/app/agent.py
# ======================================
import os
import json
import datetime
import uuid
import asyncio
import logging
import subprocess # For Railway CLI
import httpx # For Railway API calls
from typing import Dict, Any, Optional, List

# --- Observability Setup ---
SERVICE_NAME = "CI_CD_Agent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(otelTraceID)s - %(otelSpanID)s - %(message)s'
)
tracer = None
try:
    from opentelemetry import trace
    from core.observability.tracing import setup_tracing # Assuming this path is correct
    tracer = setup_tracing(SERVICE_NAME)
except ImportError:
    logging.getLogger(SERVICE_NAME).warning(
        "CI_CD_Agent: Tracing setup failed."
    )
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from interfaces.types.events import DeploymentRequestEvent, DeploymentStatusEvent

# Railway Configuration
RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN")
RAILWAY_PROJECT_ID = os.getenv("RAILWAY_PROJECT_ID")
RAILWAY_API_BASE_URL = os.getenv("RAILWAY_API_BASE_URL", "https://api.railway.app") # Default API base

# Polling Configuration
DEPLOYMENT_POLL_INTERVAL_SECONDS = int(os.getenv("DEPLOYMENT_POLL_INTERVAL_SECONDS", "30"))
DEPLOYMENT_POLL_TIMEOUT_SECONDS = int(os.getenv("DEPLOYMENT_POLL_TIMEOUT_SECONDS", "1800")) # 30 minutes

# Event channels
DEPLOYMENT_REQUEST_CHANNEL_PATTERN = "events.project.*.deployment.request"
DEPLOYMENT_STATUS_CHANNEL_TEMPLATE = "events.project.{project_id}.service.{service_name}.deployment.status"

class CICDAgent:
    def __init__(self):
        logger.info("Initializing CI_CD_Agent (V0.2)...")
        self.event_bus = EventBus()
        self.http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {RAILWAY_API_TOKEN}"},
            timeout=30.0
        )
        if not self.event_bus.redis_client:
            logger.error("CI_CD_Agent critical: EventBus not connected.")
        if not RAILWAY_API_TOKEN:
            logger.error("CI_CD_Agent critical: RAILWAY_API_TOKEN not set. API operations will fail.")
        if not RAILWAY_PROJECT_ID:
            logger.error("CI_CD_Agent critical: RAILWAY_PROJECT_ID not set. API operations will fail.")
        logger.info("CI_CD_Agent V0.2 Initialized.")

    @property
    def tracer_instance(self):
        return tracer

    def _publish_deployment_status(self, deployment_id: str, request_event: DeploymentRequestEvent, 
                                   status: str, message: Optional[str] = None, 
                                   deployment_url: Optional[str] = None, logs_url: Optional[str] = None,
                                   started_at: Optional[str] = None, completed_at: Optional[str] = None):
        if self.event_bus.redis_client:
            status_event = DeploymentStatusEvent(
                event_type="DeploymentStatusEvent",
                deployment_id=deployment_id,
                request_id=request_event["request_id"],
                project_id=request_event["project_id"],
                service_name=request_event["service_name"],
                commit_sha=request_event["commit_sha"],
                target_environment=request_event["target_environment"],
                status=status,
                message=message,
                deployment_url=deployment_url,
                logs_url=logs_url,
                started_at=started_at,
                completed_at=completed_at,
                timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            )
            channel = DEPLOYMENT_STATUS_CHANNEL_TEMPLATE.format(
                project_id=request_event["project_id"], 
                service_name=request_event["service_name"]
            )
            self.event_bus.publish(channel, status_event)
            logger.info(f"Published DeploymentStatusEvent for {deployment_id} ({request_event['service_name']}): {status}")

    async def _trigger_railway_deployment_cli(self, request_event: DeploymentRequestEvent) -> Optional[str]:
        """Triggers a Railway deployment using CLI and attempts to get a deployment ID."""
        service_name = request_event["service_name"]
        target_env = request_event["target_environment"] # Railway environments are usually part of service settings or project

        # The `railway deploy --service <service> --environment <env> --detach` command is more explicit.
        # `railway up` might also work if the service is correctly linked.
        # We need to capture the deployment ID from the CLI output.
        # Railway CLI output for `deploy --detach` might give: "✔ Deployment triggered: <deployment_id>"
        command = ["railway", "deploy", "--service", service_name, "--detach"]
        # If your Railway project has named environments that CLI can target:
        # command.extend(["--environment", target_env]) 
        # For commit specific deployment, if supported directly by `deploy` or by updating service source:
        # Railway usually deploys the latest from the linked branch. If you need specific commit,
        # you might need to use GitHub actions to update a specific branch Railway watches or use more advanced API calls.
        # For now, assume it deploys latest from branch linked to the service's Railway environment.

        logger.info(f"Triggering Railway CLI deployment for service '{service_name}' in env '{target_env}'.")

        env = os.environ.copy()
        if RAILWAY_API_TOKEN: env["RAILWAY_TOKEN"] = RAILWAY_API_TOKEN
        if RAILWAY_PROJECT_ID: env["RAILWAY_PROJECT_ID"] = RAILWAY_PROJECT_ID # Helps CLI context

        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
        )
        stdout, stderr = await process.communicate()
        stdout_str = stdout.decode().strip() if stdout else ""
        stderr_str = stderr.decode().strip() if stderr else ""

        if process.returncode == 0:
            logger.info(f"Railway CLI trigger successful for '{service_name}'. Output: {stdout_str}")
            # Attempt to parse deployment ID from stdout (this is an assumption about CLI output format)
            # Example: "Deployment triggered: deployment-uuid-here"
            # Or "Deployment QUEUED: ID <deployment-uuid-here>"
            for line in stdout_str.splitlines():
                if "Deployment triggered:" in line or "Deployment QUEUED: ID" in line :
                    try:
                        # More robust parsing needed based on actual CLI output
                        parts = line.split()
                        deployment_id = parts[-1] 
                        if deployment_id.startswith("<") and deployment_id.endswith(">"): # e.g. <id>
                            deployment_id = deployment_id[1:-1]
                        logger.info(f"Extracted Railway Deployment ID: {deployment_id} for service {service_name}")
                        return deployment_id
                    except IndexError:
                        logger.warning(f"Could not parse deployment ID from CLI output line: {line}")
            logger.warning(f"Could not extract deployment ID from Railway CLI output for '{service_name}'. Polling may not be possible.")
            return "cli_trigger_successful_no_id" # Indicates trigger worked but no ID for polling
        else:
            logger.error(f"Railway CLI trigger failed for '{service_name}'. Code: {process.returncode}. Error: {stderr_str}. Output: {stdout_str}")
            return None


    async def poll_railway_deployment_status(self, railway_deployment_id: str, service_name: str) -> Dict[str, Any]:
        """Polls Railway API for the status of a specific deployment."""
        if not RAILWAY_PROJECT_ID:
            logger.error("RAILWAY_PROJECT_ID not set, cannot poll deployment status via API.")
            return {"status": "UNKNOWN", "message": "RAILWAY_PROJECT_ID not configured for API polling."}

        # Conceptual Railway API endpoint for a specific deployment
        # Actual endpoint structure needs to be verified from Railway API documentation
        api_url = f"{RAILWAY_API_BASE_URL}/v2/projects/{RAILWAY_PROJECT_ID}/deployments/{railway_deployment_id}"
        # Or it might be simpler: /v2/deployments/{railway_deployment_id} if deployment IDs are globally unique

        logger.debug(f"Polling Railway API for deployment {railway_deployment_id} at {api_url}")
        try:
            response = await self.http_client.get(api_url)
            response.raise_for_status()
            data = response.json()
            logger.debug(f"Railway API response for {railway_deployment_id}: {data}")

            # Parse status from Railway API response (this is highly dependent on Railway's actual API structure)
            # Common statuses: BUILDING, DEPLOYING, SUCCESS, FAILED, CANCELED, REMOVING
            # We map them to our DeploymentStatusEvent statuses
            railway_status = data.get("status", "UNKNOWN").upper()
            deployment_url = data.get("url") # Or data.get("domains",[{}])[0].get("name")
            logs_url_railway = f"https://railway.app/project/{RAILWAY_PROJECT_ID}/service/{service_name}/deployments/{railway_deployment_id}" # Construct logs URL

            final_status = "UNKNOWN"
            message = f"Railway status: {railway_status}"

            if railway_status in ["SUCCESS", "ACTIVE", "DEPLOYED", "COMPLETED"]: # Adjust to actual Railway success states
                final_status = "SUCCESSFUL"
            elif railway_status in ["FAILED", "CRASHED", "ERROR", "REMOVED"]: # Adjust to actual Railway failure states
                final_status = "FAILED"
                message = data.get("error", {}).get("message", f"Railway deployment failed with status: {railway_status}")
            elif railway_status in ["BUILDING", "DEPLOYING", "INIT", "QUEUED", "PENDING", "PREPARING"]:
                final_status = "IN_PROGRESS"
            else: # Other states like CANCELED, TIMEOUT etc.
                final_status = "FAILED" # Or map to a more specific status if needed
                message = f"Railway deployment ended with unhandled status: {railway_status}"

            return {
                "status": final_status, 
                "message": message, 
                "deployment_url": deployment_url, 
                "logs_url": logs_url_railway,
                "raw_railway_status": railway_status
            }

        except httpx.HTTPStatusError as e:
            logger.error(f"Railway API error polling deployment {railway_deployment_id}: {e.response.status_code} - {e.response.text}")
            return {"status": "API_ERROR", "message": f"Railway API error: {e.response.status_code}"}
        except Exception as e:
            logger.error(f"Error polling Railway deployment {railway_deployment_id}: {e}", exc_info=True)
            return {"status": "POLL_ERROR", "message": f"Polling error: {e}"}


    async def execute_deployment_with_polling(self, request_event: DeploymentRequestEvent):
        deployment_id_internal = str(uuid.uuid4()) # Our internal ID for this deployment attempt
        started_at_iso = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"

        span_name = f"cicd_agent.execute_deployment:{request_event['service_name']}"
        parent_span_context = None # Extract if available in request_event

        if not self.tracer_instance: # Fallback
            await self._execute_deployment_with_polling_logic(deployment_id_internal, request_event, started_at_iso)
            return

        with self.tracer_instance.start_as_current_span(span_name, context=parent_span_context) as span:
            span.set_attributes({
                "cicd.deployment_id": deployment_id_internal,
                "cicd.request_id": request_event["request_id"],
                "cicd.project_id": request_event["project_id"],
                "cicd.service_name": request_event["service_name"],
                "cicd.commit_sha": request_event["commit_sha"],
                "cicd.target_environment": request_event["target_environment"]
            })
            logger.info(f"Starting full deployment process {deployment_id_internal} for service '{request_event['service_name']}'")
            await self._execute_deployment_with_polling_logic(deployment_id_internal, request_event, started_at_iso)

    async def _execute_deployment_with_polling_logic(self, deployment_id_internal: str, request_event: DeploymentRequestEvent, started_at_iso: str):
        self._publish_deployment_status(deployment_id_internal, request_event, "STARTED", started_at=started_at_iso, message="Deployment process initiated.")

        if not RAILWAY_API_TOKEN or not RAILWAY_PROJECT_ID:
            msg = "RAILWAY_API_TOKEN or RAILWAY_PROJECT_ID not configured. Cannot proceed with Railway API interactions."
            logger.error(f"Deployment {deployment_id_internal}: {msg}")
            self._publish_deployment_status(deployment_id_internal, request_event, "FAILED", started_at=started_at_iso, message=msg)
            if self.tracer_instance: trace.get_current_span().set_status(trace.Status(trace.StatusCode.ERROR, msg))
            return

        # Step 1: Trigger deployment via CLI and try to get Railway's deployment ID
        railway_deployment_id = await self._trigger_railway_deployment_cli(request_event)

        if not railway_deployment_id:
            msg = "Failed to trigger Railway deployment via CLI."
            logger.error(f"Deployment {deployment_id_internal}: {msg}")
            self._publish_deployment_status(deployment_id_internal, request_event, "FAILED", started_at=started_at_iso, message=msg)
            if self.tracer_instance: trace.get_current_span().set_status(trace.Status(trace.StatusCode.ERROR, msg))
            return

        if railway_deployment_id == "cli_trigger_successful_no_id":
            # CLI trigger worked but we couldn't parse an ID for polling.
            # We consider this as "trigger successful" for V0.1 of CLI interaction.
            # A more robust solution would ensure the CLI always returns a usable ID or use the API to trigger.
            msg = "Railway deployment trigger command succeeded, but could not extract Railway deployment ID for status polling. Monitor Railway directly."
            logger.warning(f"Deployment {deployment_id_internal}: {msg}")
            self._publish_deployment_status(deployment_id_internal, request_event, "SUCCESSFUL", # Trigger was successful
                                           started_at=started_at_iso, 
                                           completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
                                           message=msg)
            if self.tracer_instance: trace.get_current_span().set_attribute("cicd.warning", "no_railway_deployment_id_for_polling")
            return

        # Step 2: Poll for status using the extracted railway_deployment_id
        self._publish_deployment_status(deployment_id_internal, request_event, "IN_PROGRESS", started_at=started_at_iso, 
                                       message=f"Deployment triggered on Railway (ID: {railway_deployment_id}). Polling status...")

        polling_start_time = time.monotonic()
        final_poll_status = {"status": "UNKNOWN", "message": "Polling did not complete."}

        while True:
            if time.monotonic() - polling_start_time > DEPLOYMENT_POLL_TIMEOUT_SECONDS:
                final_poll_status = {"status": "FAILED", "message": "Polling timed out after {DEPLOYMENT_POLL_TIMEOUT_SECONDS}s."}
                logger.error(f"Deployment {deployment_id_internal}: {final_poll_status['message']}")
                if self.tracer_instance: trace.get_current_span().set_attribute("cicd.polling_timeout", True)
                break

            poll_result = await self.poll_railway_deployment_status(railway_deployment_id, request_event["service_name"])
            current_poll_status = poll_result.get("status", "UNKNOWN")

            if self.tracer_instance: trace.get_current_span().add_event("PollRailwayStatus", {"railway_status": poll_result.get("raw_railway_status"), "mapped_status": current_poll_status})

            if current_poll_status == "SUCCESSFUL":
                final_poll_status = poll_result
                logger.info(f"Deployment {deployment_id_internal}: Polling confirmed SUCCESSFUL state from Railway.")
                break
            elif current_poll_status in ["FAILED", "API_ERROR", "POLL_ERROR"]:
                final_poll_status = poll_result
                logger.error(f"Deployment {deployment_id_internal}: Polling confirmed FAILED state from Railway or error: {poll_result.get('message')}")
                break
            elif current_poll_status == "IN_PROGRESS":
                logger.info(f"Deployment {deployment_id_internal}: Still IN_PROGRESS on Railway (Raw: {poll_result.get('raw_railway_status')}). Waiting...")
                self._publish_deployment_status(deployment_id_internal, request_event, "IN_PROGRESS", 
                                               message=f"Railway status: {poll_result.get('raw_railway_status')}", 
                                               started_at=started_at_iso,
                                               logs_url=poll_result.get("logs_url"))
            else: # UNKNOWN or other states
                logger.warning(f"Deployment {deployment_id_internal}: Received unhandled poll status '{current_poll_status}'. Will retry.")

            await asyncio.sleep(DEPLOYMENT_POLL_INTERVAL_SECONDS)

        # Publish final status based on polling
        self._publish_deployment_status(
            deployment_id_internal, request_event,
            final_poll_status["status"],
            message=final_poll_status.get("message"),
            deployment_url=final_poll_status.get("deployment_url"),
            logs_url=final_poll_status.get("logs_url"),
            started_at=started_at_iso,
            completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        )
        if self.tracer_instance:
            final_status_for_trace = final_poll_status["status"]
            if final_status_for_trace == "SUCCESSFUL":
                trace.get_current_span().set_status(trace.Status(trace.StatusCode.OK))
            else:
                trace.get_current_span().set_status(trace.Status(trace.StatusCode.ERROR, final_poll_status.get("message", "Deployment ended unsuccessfully")))


    async def handle_deployment_request_event(self, event_data_str: str):
        span_name = "cicd_agent.handle_deployment_request_event"
        parent_context = None
        if not self.tracer_instance: # Fallback
            await self._handle_deployment_request_event_logic(event_data_str)
            return

        with self.tracer_instance.start_as_current_span(span_name, context=parent_context) as span:
            try:
                request: DeploymentRequestEvent = json.loads(event_data_str)
                if not (request.get("event_type") == "DeploymentRequestEvent" and
                        all(k in request for k in ["request_id", "project_id", "service_name", "commit_sha", "target_environment"])):
                    logger.error(f"Malformed DeploymentRequestEvent: {event_data_str[:200]}")
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "Malformed event"))
                    return

                span.set_attributes({
                    "messaging.system": "redis",
                    "cicd.request_id": request["request_id"]
                })
                logger.info(f"CI_CD_Agent handling DeploymentRequestEvent: {request['request_id']} for service {request['service_name']}")

                asyncio.create_task(self.execute_deployment_with_polling(request))

            except json.JSONDecodeError:
                logger.error(f"Could not decode JSON from event: {event_data_str[:200]}")
                span.set_status(trace.Status(trace.StatusCode.ERROR, "JSON decode error"))
            except Exception as e:
                logger.error(f"Error handling DeploymentRequestEvent: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Event handling failed"))


    async def main_event_loop(self):
        # ... (event loop structure as defined in V0.1, calling self.handle_deployment_request_event) ...
        if not self.event_bus.redis_client:
            logger.critical("CI_CD_Agent: EventBus not connected. Worker cannot start.")
            await asyncio.sleep(60)
            return

        pubsub = self.event_bus.subscribe_to_channel(DEPLOYMENT_REQUEST_CHANNEL_PATTERN)
        if not pubsub:
            logger.critical(f"CI_CD_Agent: Failed to subscribe to {DEPLOYMENT_REQUEST_CHANNEL_PATTERN}. Worker cannot start.")
            await asyncio.sleep(60)
            return

        logger.info(f"CI_CD_Agent worker subscribed to {DEPLOYMENT_REQUEST_CHANNEL_PATTERN}, listening for deployment requests...")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "pmessage": # pmessage for pattern subscriptions
                    await self.handle_deployment_request_event(message["data"])
                await asyncio.sleep(0.01)
        except KeyboardInterrupt:
            logger.info("CI_CD_Agent event loop interrupted.")
        except Exception as e:
            logger.error(f"Critical error in CI_CD_Agent event loop: {e}", exc_info=True)
        finally:
            logger.info("CI_CD_Agent shutting down pubsub and HTTP client...")
            if pubsub:
                try: 
                    await asyncio.to_thread(pubsub.punsubscribe, DEPLOYMENT_REQUEST_CHANNEL_PATTERN)
                    await asyncio.to_thread(pubsub.close)
                except Exception as e_close:
                     logger.error(f"Error closing pubsub for CI_CD_Agent: {e_close}")
            await self.http_client.aclose() # Close httpx client
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
        logger.critical(f"CI_CD_Agent failed to start or unhandled error in __main__: {e}", exc_info=True)
