```python
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
from typing import Dict, Any, Optional

# --- Observability Setup ---
SERVICE_NAME = "CI_CD_Agent"
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
        "CI_CD_Agent: Tracing setup failed."
    )
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from interfaces.types.events import DeploymentRequestEvent, DeploymentStatusEvent

RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN") # Needs to be set for Railway CLI to work
RAILWAY_PROJECT_ID = os.getenv("RAILWAY_PROJECT_ID") # Optional: if needed to link/specify project context

# Event channels
DEPLOYMENT_REQUEST_CHANNEL_PATTERN = "events.project.*.deployment.request" # Listens for deployment requests
DEPLOYMENT_STATUS_CHANNEL_TEMPLATE = "events.project.{project_id}.service.{service_name}.deployment.status"


class CICDAgent:
    def __init__(self):
        logger.info("Initializing CI_CD_Agent...")
        self.event_bus = EventBus()
        if not self.event_bus.redis_client:
            logger.error("CI_CD_Agent critical: EventBus not connected.")
        if not RAILWAY_API_TOKEN:
            logger.warning("RAILWAY_API_TOKEN not set. Railway CLI operations will likely fail.")
        logger.info("CI_CD_Agent Initialized.")

    @property
    def tracer_instance(self):
        return tracer

    def _publish_deployment_status(self, deployment_id: str, request: DeploymentRequestEvent, 
                                   status: str, message: Optional[str] = None, 
                                   deployment_url: Optional[str] = None, logs_url: Optional[str] = None,
                                   started_at: Optional[str] = None, completed_at: Optional[str] = None):
        if self.event_bus.redis_client:
            status_event = DeploymentStatusEvent(
                event_type="DeploymentStatusEvent",
                deployment_id=deployment_id,
                request_id=request["request_id"],
                project_id=request["project_id"],
                service_name=request["service_name"],
                commit_sha=request["commit_sha"],
                target_environment=request["target_environment"],
                status=status,
                message=message,
                deployment_url=deployment_url,
                logs_url=logs_url,
                started_at=started_at,
                completed_at=completed_at,
                timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
            )
            channel = DEPLOYMENT_STATUS_CHANNEL_TEMPLATE.format(
                project_id=request["project_id"], 
                service_name=request["service_name"]
            )
            self.event_bus.publish(channel, status_event)
            logger.info(f"Published DeploymentStatusEvent for {deployment_id}: {status}")

    async def _run_railway_command(self, command: List[str], deployment_id: str) -> Dict[str, Any]:
        """Helper to run Railway CLI commands."""
        logger.info(f"Deployment {deployment_id}: Running Railway command: {' '.join(command)}")
        
        env = os.environ.copy()
        if RAILWAY_API_TOKEN: # Ensure token is in environment for the subprocess
            env["RAILWAY_TOKEN"] = RAILWAY_API_TOKEN
        if RAILWAY_PROJECT_ID: # If project ID is explicitly set for context
            env["RAILWAY_PROJECT_ID"] = RAILWAY_PROJECT_ID

        # Using asyncio.create_subprocess_exec for non-blocking execution
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await process.communicate() # Wait for command to complete

        stdout_str = stdout.decode().strip() if stdout else ""
        stderr_str = stderr.decode().strip() if stderr else ""
        
        if process.returncode == 0:
            logger.info(f"Deployment {deployment_id}: Railway command successful. Stdout: {stdout_str[:200]}")
            return {"success": True, "stdout": stdout_str, "stderr": stderr_str}
        else:
            logger.error(f"Deployment {deployment_id}: Railway command failed with code {process.returncode}. Stderr: {stderr_str}. Stdout: {stdout_str[:200]}")
            return {"success": False, "stdout": stdout_str, "stderr": stderr_str, "exit_code": process.returncode}

    async def execute_deployment(self, request: DeploymentRequestEvent):
        deployment_id = str(uuid.uuid4())
        span_name = f"cicd_agent.execute_deployment:{request['service_name']}"
        
        parent_span_context = None # Extract from request if available
        if not self.tracer_instance: # Fallback
            await self._execute_deployment_logic(deployment_id, request)
            return

        with self.tracer_instance.start_as_current_span(span_name, context=parent_span_context) as span:
            span.set_attributes({
                "cicd.deployment_id": deployment_id,
                "cicd.request_id": request["request_id"],
                "cicd.project_id": request["project_id"],
                "cicd.service_name": request["service_name"],
                "cicd.commit_sha": request["commit_sha"],
                "cicd.target_environment": request["target_environment"]
            })
            logger.info(f"Starting deployment {deployment_id} for service '{request['service_name']}' to env '{request['target_environment']}'")
            await self._execute_deployment_logic(deployment_id, request)


    async def _execute_deployment_logic(self, deployment_id: str, request: DeploymentRequestEvent):
        started_at = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
        self._publish_deployment_status(deployment_id, request, "STARTED", started_at=started_at, message="Deployment process initiated.")

        if not RAILWAY_API_TOKEN:
            logger.error(f"Deployment {deployment_id}: Missing RAILWAY_API_TOKEN. Cannot proceed.")
            self._publish_deployment_status(deployment_id, request, "FAILED", started_at=started_at, message="RAILWAY_API_TOKEN not configured.")
            if self.tracer_instance: trace.get_current_span().set_status(trace.Status(trace.StatusCode.ERROR, "Missing RAILWAY_API_TOKEN"))
            return

        # Construct Railway CLI command
        # `railway up` usually deploys the latest commit if linked to a project and service.
        # It can also redeploy a specific existing deployment.
        # For deploying a specific commit to a service:
        # `railway deploy --service <service_id_or_name> --environment <environment_name> --detach`
        # The '--commit <commit_sha>' flag might be needed if Railway doesn't automatically pick latest from branch.
        # For V0.1, `railway up --service <name>` often triggers a deploy of the latest linked commit.
        # If your Railway service is configured to watch a specific branch, `railway up` might just trigger a re-poll/re-deploy.
        # A more explicit command could be `railway redeploy --service <service_name> --environment <env_name>`
        # Let's use `railway up --service <service_name> --detach` which is common for CI.
        # The --detach flag makes the CLI exit after triggering, not waiting for completion.
        # We'd then rely on Railway webhooks or polling for final status if needed,
        # but for V0.1, triggering is the main goal.
        
        # Note: Railway environments are often part of the project setup itself,
        # the CLI might pick up the environment based on how the project is linked.
        # If RAILWAY_PROJECT_ID is set, it helps.
        # The `--environment` flag for `railway up` or `deploy` would target a specific Railway environment.
        
        command = ["railway", "up", "--service", request["service_name"], "--detach"]
        # If you have specific Railway environments (e.g., staging, production)
        # And your services are configured with these environments in Railway:
        # command.extend(["--environment", request["target_environment"]])


        logger.info(f"Deployment {deployment_id}: Triggering Railway deployment for service '{request['service_name']}'.")
        self._publish_deployment_status(deployment_id, request, "IN_PROGRESS", started_at=started_at, message="Triggering deployment on Railway.")
        
        cli_result = await self._run_railway_command(command, deployment_id)

        if cli_result["success"]:
            logger.info(f"Deployment {deployment_id}: Railway deployment triggered successfully for '{request['service_name']}'.")
            # Note: `railway up --detach` returns quickly. True deployment status comes from Railway platform.
            # For V0.1, we'll mark as successful if the trigger command succeeded.
            # A more advanced version would use Railway API/webhooks to get actual deployment completion status.
            self._publish_deployment_status(
                deployment_id, request, "SUCCESSFUL", # This indicates trigger success
                started_at=started_at,
                completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
                message="Railway deployment trigger command succeeded. Monitor Railway for actual status.",
                # deployment_url= extract from Railway if possible, or known pattern
            )
            if self.tracer_instance: trace.get_current_span().set_attribute("cicd.railway_trigger_stdout", cli_result["stdout"][:500])
        else:
            error_message = f"Railway deployment trigger failed. CLI Stderr: {cli_result['stderr']}. CLI Stdout: {cli_result['stdout']}"
            logger.error(f"Deployment {deployment_id}: {error_message}")
            self._publish_deployment_status(
                deployment_id, request, "FAILED", 
                started_at=started_at,
                completed_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
                message=error_message
            )
            if self.tracer_instance:
                trace.get_current_span().set_attribute("cicd.railway_trigger_error", error_message)
                trace.get_current_span().set_status(trace.Status(trace.StatusCode.ERROR, "Railway trigger failed"))

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
                    # Other attributes are set in execute_deployment
                })
                logger.info(f"CI_CD_Agent handling DeploymentRequestEvent: {request['request_id']} for service {request['service_name']}")
                
                # Schedule deployment (don't block the event listener)
                asyncio.create_task(self.execute_deployment(request))

            except json.JSONDecodeError:
                logger.error(f"Could not decode JSON from event: {event_data_str[:200]}")
                span.set_status(trace.Status(trace.StatusCode.ERROR, "JSON decode error"))
            except Exception as e:
                logger.error(f"Error handling DeploymentRequestEvent: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Event handling failed"))

    async def main_event_loop(self):
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
                if message and message["type"] == "pmessage":
                    await self.handle_deployment_request_event(message["data"])
                await asyncio.sleep(0.01)
        except KeyboardInterrupt:
            logger.info("CI_CD_Agent event loop interrupted.")
        except Exception as e:
            logger.error(f"Critical error in CI_CD_Agent event loop: {e}", exc_info=True)
        finally:
            logger.info("CI_CD_Agent shutting down pubsub...")
            if pubsub:
                try: 
                    await asyncio.to_thread(pubsub.punsubscribe, DEPLOYMENT_REQUEST_CHANNEL_PATTERN)
                    await asyncio.to_thread(pubsub.close)
                except Exception as e_close:
                     logger.error(f"Error closing pubsub for CI_CD_Agent: {e_close}")
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
```
