```python
# =========================================
# 📁 agents/GovernanceAgent/app/agent.py
# =========================================
import os
import json
import datetime
import uuid
import asyncio
import logging
from typing import Dict, Any, Optional, List

# --- Observability Setup ---
SERVICE_NAME = "GovernanceAgent"
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
        "GovernanceAgent: Tracing setup failed."
    )
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from interfaces.types.events import (
    AuditLogEntry, SLAMetric, SLAViolationEvent, GovernanceAlertEvent, # New types
    # Import other event types this agent might specifically audit
    DagDefinitionCreatedEvent, DagExecutionStatusEvent, TaskStatusUpdateEvent,
    TestFailedEvent, PatchSuggestedEvent, SecurityScanResultEvent, DeploymentStatusEvent
)

# Configuration
GOVERNANCE_AUDIT_SINK_TYPE = os.getenv("GOVERNANCE_AUDIT_SINK_TYPE", "console_log") # e.g., console_log, s3_audit
SLA_DEFINITIONS_JSON_STR = os.getenv("SLA_DEFINITIONS_JSON") # e.g., '{ "dag_max_duration_seconds": {"project_alpha": 3600} }'

# Event channels this agent listens to - broad subscription
AUDITABLE_EVENT_PATTERNS = [
    "events.project.*", # Catches most project-specific operational events
    "events.system.*",  # For system-level events
    "events.agent.*.lifecycle", # Agent startup/shutdown etc.
    # Add more specific patterns if needed
]
GOVERNANCE_ALERT_CHANNEL = "events.system.governance.alerts"


class GovernanceAgent:
    def __init__(self):
        logger.info("Initializing GovernanceAgent...")
        self.event_bus = EventBus()
        self.sla_definitions: Dict[str, Any] = {}
        if SLA_DEFINITIONS_JSON_STR:
            try:
                self.sla_definitions = json.loads(SLA_DEFINITIONS_JSON_STR)
                logger.info(f"Loaded SLA definitions: {self.sla_definitions}")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse SLA_DEFINITIONS_JSON: {SLA_DEFINITIONS_JSON_STR}")
        
        if not self.event_bus.redis_client:
            logger.error("GovernanceAgent critical: EventBus not connected.")
        logger.info(f"GovernanceAgent Initialized. Audit Sink: {GOVERNANCE_AUDIT_SINK_TYPE}")

    @property
    def tracer_instance(self):
        return tracer

    def _create_audit_log(self, original_event: Dict[str, Any], received_channel: str) -> AuditLogEntry:
        """Creates a standardized audit log entry from an incoming event."""
        # Sanitize and select key details from the original event for the audit log
        details_to_log = {
            k: v for k, v in original_event.items() 
            if k not in ["event_type", "timestamp"] and isinstance(v, (str, int, float, bool, list, dict))
        }
        # Potentially truncate large fields in details_to_log
        for k, v in details_to_log.items():
            if isinstance(v, str) and len(v) > 256:
                details_to_log[k] = v[:253] + "..."
            elif isinstance(v, list) and len(v) > 10:
                 details_to_log[k] = [str(item)[:100] for item in v[:10]] + ["... (truncated)"]


        return AuditLogEntry(
            event_type="AuditEvent", # This is the type of event this agent *creates*
            audit_id=str(uuid.uuid4()),
            timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            source_event_type=original_event.get("event_type", "UnknownSourceEvent"),
            source_event_id=original_event.get("event_id") or original_event.get("request_id") or original_event.get("dag_id"),
            service_name=original_event.get("service_name"), # If original event has it
            project_id=original_event.get("project_id"),
            commit_sha=original_event.get("commit_sha"),
            user_or_actor=original_event.get("triggered_by") or original_event.get("author"),
            action_taken=f"Event '{original_event.get('event_type', 'Unknown')}' occurred on channel '{received_channel}'",
            details=details_to_log,
            policy_check_results=None # Placeholder for future policy checks
        )

    async def persist_audit_log(self, audit_entry: AuditLogEntry):
        """Persists the audit log. For V0.1, logs to console. Future: S3, Database."""
        log_payload_str = json.dumps(audit_entry) # For structured logging
        logger.info(f"AUDIT_LOG: {log_payload_str}") # This makes it easy to filter in log management

        if GOVERNANCE_AUDIT_SINK_TYPE == "s3_audit_placeholder": # Conceptual
            # Placeholder for S3 logic
            # s3_key = f"audit_logs/{audit_entry['timestamp_date']}/{audit_entry['audit_id']}.json"
            # logger.info(f"S3_AUDIT_PLACEHOLDER: Would write to S3 path: {s3_key}")
            pass
    
    def _check_slas(self, event_data: Dict[str, Any]):
        """Placeholder for basic SLA checking based on event data."""
        event_type = event_data.get("event_type")
        project_id = event_data.get("project_id")

        if event_type == "DagExecutionStatusEvent" and event_data.get("status", "").startswith("COMPLETED"):
            dag_id = event_data["dag_id"]
            started_at_str = event_data.get("started_at")
            completed_at_str = event_data.get("completed_at")
            
            if started_at_str and completed_at_str:
                try:
                    started = datetime.datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
                    completed = datetime.datetime.fromisoformat(completed_at_str.replace("Z", "+00:00"))
                    duration_seconds = (completed - started).total_seconds()
                    
                    logger.debug(f"DAG {dag_id} for project {project_id} duration: {duration_seconds}s")
                    
                    # Example SLA check
                    sla_config = self.sla_definitions.get("dag_max_duration_seconds", {})
                    project_sla_duration = sla_config.get(project_id, sla_config.get("default", 7200)) # Default 2hrs

                    if duration_seconds > project_sla_duration:
                        violation_details = (
                            f"DAG '{dag_id}' in project '{project_id}' exceeded SLA. "
                            f"Duration: {duration_seconds:.2f}s, Threshold: {project_sla_duration}s."
                        )
                        logger.warning(violation_details)
                        self._publish_sla_violation(
                            sla_name="dag_execution_time_sla",
                            metric_name="dag_execution_duration",
                            observed_value=duration_seconds,
                            threshold_value=float(project_sla_duration),
                            project_id=project_id,
                            details=violation_details
                        )
                except ValueError:
                    logger.error(f"Could not parse timestamps for DAG {dag_id} to check SLA.")
        # Add more SLA checks for other event types or metrics
    
    def _publish_sla_violation(self, sla_name: str, metric_name: str, observed_value: float, 
                               threshold_value: float, project_id: Optional[str], details: str):
        if self.event_bus.redis_client:
            alert_event = SLAViolationEvent(
                event_type="SLAViolationEvent",
                alert_id=str(uuid.uuid4()),
                timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
                sla_name=sla_name,
                metric_name=metric_name,
                observed_value=observed_value,
                threshold_value=threshold_value,
                project_id=project_id,
                details=details
            )
            self.event_bus.publish(GOVERNANCE_ALERT_CHANNEL, alert_event)
            logger.warning(f"Published SLAViolationEvent: {details}")


    async def handle_generic_event(self, channel: str, event_data_str: str):
        span_name = "governance_agent.handle_generic_event"
        parent_context = None 
        
        if not self.tracer_instance: # Fallback
            await self._handle_generic_event_logic(channel, event_data_str)
            return

        with self.tracer_instance.start_as_current_span(span_name, context=parent_context) as span:
            span.set_attributes({
                "messaging.system": "redis",
                "messaging.destination.name": channel, # Actual channel from pmessage
                "messaging.operation": "process"
            })
            try:
                await self._handle_generic_event_logic(channel, event_data_str)
            except Exception as e: # Catchall
                logger.error(f"Unhandled error in _handle_generic_event_logic from channel {channel}: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Generic event handling logic failed"))

    async def _handle_generic_event_logic(self, channel: str, event_data_str: str):
        logger.debug(f"GovernanceAgent processing event from channel '{channel}': {message_summary(event_data_str)}")
        try:
            event_data = json.loads(event_data_str)
            event_type = event_data.get("event_type", "UnknownEvent")

            if self.tracer_instance:
                trace.get_current_span().set_attribute("event.type", event_type)
                trace.get_current_span().set_attribute("event.project_id", event_data.get("project_id"))

            # 1. Create and persist audit log
            audit_entry = self._create_audit_log(event_data, channel)
            await self.persist_audit_log(audit_entry) # Make async if persist_audit_log does I/O

            # 2. Perform SLA checks (basic example)
            self._check_slas(event_data)

            # 3. Placeholder for policy/ethics checks
            # if event_type == "DagDefinitionCreatedEvent":
            #    self._check_dag_against_policies(event_data.get("dag"))
            # elif event_type == "PatchSuggestedEvent":
            #    self._check_patch_ethics(event_data.get("suggestions"))
            
            logger.debug(f"Finished processing event '{event_type}' for governance.")

        except json.JSONDecodeError:
            logger.error(f"Could not decode JSON for audit/SLA from channel '{channel}': {message_summary(event_data_str)}")
            if self.tracer_instance: trace.get_current_span().set_status(trace.Status(trace.StatusCode.ERROR, "JSONDecodeError in governance handling"))
        except Exception as e:
            logger.error(f"Error processing event for governance from channel '{channel}': {e}", exc_info=True)
            if self.tracer_instance:
                trace.get_current_span().record_exception(e)
                trace.get_current_span().set_status(trace.Status(trace.StatusCode.ERROR, "Governance event processing failed"))

    async def main_event_loop(self):
        if not self.event_bus.redis_client:
            logger.critical("GovernanceAgent: EventBus not connected. Worker cannot start.")
            await asyncio.sleep(60)
            return

        pubsub = self.event_bus.redis_client.pubsub()
        subscribed_ok = False
        try:
            for pattern in AUDITABLE_EVENT_PATTERNS:
                await asyncio.to_thread(pubsub.psubscribe, pattern)
                logger.info(f"GovernanceAgent subscribed to pattern '{pattern}'")
            subscribed_ok = True
        except redis.exceptions.RedisError as e:
            logger.critical(f"GovernanceAgent: Failed to psubscribe: {e}. Worker cannot start.")
            return
        
        if not subscribed_ok:
             logger.critical("GovernanceAgent: No subscriptions successful. Worker exiting.")
             return

        logger.info(f"GovernanceAgent worker started, listening for auditable events...")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "pmessage":
                    # Start a new trace for each message
                    span_name = "governance_agent.process_auditable_event_from_bus"
                    if not self.tracer_instance:
                         await self.handle_generic_event(message["channel"], message["data"])
                    else:
                        with self.tracer_instance.start_as_current_span(span_name) as span:
                            span.set_attribute("messaging.source.channel", message.get("channel"))
                            await self.handle_generic_event(message["channel"], message["data"])
                await asyncio.sleep(0.01)
        except KeyboardInterrupt:
            logger.info("GovernanceAgent event loop interrupted.")
        except Exception as e:
            logger.error(f"Critical error in GovernanceAgent event loop: {e}", exc_info=True)
        finally:
            logger.info("GovernanceAgent shutting down pubsub...")
            if pubsub:
                try: 
                    for pattern in AUDITABLE_EVENT_PATTERNS:
                       await asyncio.to_thread(pubsub.punsubscribe, pattern)
                    await asyncio.to_thread(pubsub.close)
                except Exception as e_close:
                     logger.error(f"Error closing pubsub for GovernanceAgent: {e_close}")
            logger.info("GovernanceAgent shutdown complete.")

async def main_async_runner():
    agent = GovernanceAgent()
    await agent.main_event_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main_async_runner())
    except KeyboardInterrupt:
        logger.info("GovernanceAgent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"GovernanceAgent failed to start or unhandled error in __main__: {e}", exc_info=True)
```
