# =========================================
# 📁 agents/GovernanceAgent/agent.py
# =========================================
import os
import json
import datetime
import uuid
import asyncio
import logging
from typing import Dict, Any, Optional, List, cast # Added cast for TypedDict conversion

# --- Observability Setup ---
SERVICE_NAME = "GovernanceAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# Basic logging config (OTel LoggingInstrumentor will enhance it)
logging.basicConfig(
    level=LOG_LEVEL,
    format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s'
    # Removed otelTraceID and otelSpanID from basicConfig format string, 
    # as LoggingInstrumentor handles adding those if OTel is active.
)
_tracer = None 
_trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing # Assuming this path is correct via PYTHONPATH
    _tracer = setup_tracing(SERVICE_NAME) # setup_tracing returns a Tracer instance
    _trace_api = otel_trace_api # This is the opentelemetry.trace module, for Status etc.
except ImportError:
    # This warning is fine, means no tracing if OTel is not installed/configured
    logging.getLogger(SERVICE_NAME).warning(
        "GovernanceAgent: OpenTelemetry tracing setup failed or modules not found. Continuing without tracing."
    )
except Exception as e_otel_setup:
    # Catch any other error during setup_tracing
    logging.getLogger(SERVICE_NAME).error(
        f"GovernanceAgent: An error occurred during OpenTelemetry setup: {e_otel_setup}", exc_info=True
    )
logger = logging.getLogger(__name__) # Module-specific logger
# --- End Observability Setup ---

# Project-local imports (ensure __init__.py files and PYTHONPATH allow these)
from core.event_bus.redis_bus import EventBus, message_summary
from interfaces.types.events import (
    AuditLogEntry, SLAMetric, SLAViolationEvent, GovernanceAlertEvent,
    DagExecutionStatusEvent, # Example event to check SLA against
    # Import other specific event TypedDicts that this agent might deeply inspect or handle
    NewCommitEvent, NewArtifactEvent, SecurityScanResultEvent, TestFailedEvent, PatchSuggestedEvent
)

# Configuration
GOVERNANCE_AUDIT_SINK_TYPE = os.getenv("GOVERNANCE_AUDIT_SINK_TYPE", "console_log")
SLA_DEFINITIONS_JSON_STR = os.getenv("SLA_DEFINITIONS_JSON", '{}')

AUDITABLE_EVENT_PATTERNS_STR = os.getenv(
    "GOVERNANCE_AUDIT_PATTERNS", 
    "events.project.*,events.system.*,events.agent.*.lifecycle"
)
AUDITABLE_EVENT_PATTERNS = [p.strip() for p in AUDITABLE_EVENT_PATTERNS_STR.split(',') if p.strip()]

GOVERNANCE_ALERTS_CHANNEL = "events.system.governance.alerts"


class GovernanceAgent:
    def __init__(self):
        logger.info("Initializing GovernanceAgent...")
        self.event_bus = EventBus()
        self.sla_definitions: Dict[str, Any] = {}
        try:
            if SLA_DEFINITIONS_JSON_STR:
                self.sla_definitions = json.loads(SLA_DEFINITIONS_JSON_STR)
            logger.info(f"Loaded {len(self.sla_definitions.keys())} SLA definition group(s).")
        except json.JSONDecodeError:
            logger.error(f"Failed to parse SLA_DEFINITIONS_JSON: '{SLA_DEFINITIONS_JSON_STR}'. No SLAs will be actively monitored from this config.")
        
        if not self.event_bus.redis_client:
            logger.error("GovernanceAgent critical: EventBus not connected. Event processing will fail.")
        logger.info(f"GovernanceAgent Initialized. Audit Sink: {GOVERNANCE_AUDIT_SINK_TYPE}, Listening to: {AUDITABLE_EVENT_PATTERNS}")

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        if _tracer and _trace_api:
            span = _tracer.start_span(f"governance_agent.{operation_name}", context=parent_context)
            for k, v in attrs.items():
                span.set_attribute(k, v)
            return span
        
        class NoOpSpan: # Fallback if no tracer
            def __enter__(self): return self
            def __exit__(self, exc_type, exc_val, exc_tb): pass
            def set_attribute(self, key, value): pass
            def record_exception(self, exception, attributes=None): pass
            def set_status(self, status): pass
            def end(self): pass
        return NoOpSpan()

    def _create_audit_log_entry(self, original_event: Dict[str, Any], received_channel: str) -> AuditLogEntry:
        source_event_type = original_event.get("event_type", "UnknownSourceEvent")
        details_to_log = {}
        for k, v in original_event.items():
            if k not in ["event_type", "timestamp"] and isinstance(v, (str, int, float, bool, list, dict)):
                if isinstance(v, str) and len(v) > 500:
                    details_to_log[k] = v[:497] + "..."
                elif isinstance(v, list) and len(v) > 20:
                    details_to_log[k] = [str(item_elem)[:100] for item_elem in v[:20]] + ["... (list truncated)"]
                elif isinstance(v, dict) and len(json.dumps(v)) > 1024: # Check serialized size for dicts
                    details_to_log[k] = {"summary": "dict_truncated_due_to_size", "keys": list(v.keys())[:10]}
                else:
                    details_to_log[k] = v
        
        # Attempt to get a more specific ID from the event
        source_id_fields = ["event_id", "request_id", "dag_id", "deployment_id", "audit_id"]
        source_event_id = next((original_event.get(field) for field in source_id_fields if original_event.get(field)), None)


        return AuditLogEntry(
            event_type="AuditEvent",
            audit_id=str(uuid.uuid4()),
            timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            source_event_type=source_event_type,
            source_event_id=source_event_id,
            service_name=original_event.get("service_name"), 
            project_id=original_event.get("project_id"),
            commit_sha=original_event.get("commit_sha"),
            user_or_actor=original_event.get("triggered_by") or original_event.get("requested_by") or original_event.get("author"),
            action_description=f"Event '{source_event_type}' observed on channel '{received_channel}'.",
            event_details=details_to_log,
            policy_check_results=self._perform_policy_checks(original_event), # Returns Optional[Dict[str,str]]
            severity=original_event.get("severity", "INFO") if source_event_type != "AuditEvent" else "INFO" # Audit severity
        )

    async def _persist_audit_log(self, audit_entry: AuditLogEntry):
        log_payload_str = json.dumps(audit_entry) # Ensure it's serializable
        logger.info(f"GOVERNANCE_AUDIT_LOG: {log_payload_str}") 

        if GOVERNANCE_AUDIT_SINK_TYPE == "s3_audit_placeholder":
            # Placeholder for S3 logic, ensure project_id and source_event_type are filesystem-safe
            project_id_path = "".join(c if c.isalnum() or c in ['-', '_'] else '_' for c in audit_entry.get("project_id", "system"))
            event_type_path = "".join(c if c.isalnum() or c in ['-', '_'] else '_' for c in audit_entry['source_event_type'])
            ts_obj = datetime.datetime.fromisoformat(audit_entry["timestamp"].replace("Z","+00:00"))
            date_path = ts_obj.strftime('%Y/%m/%d')
            s3_key = f"audit_trail/{project_id_path}/{event_type_path}/{date_path}/{audit_entry['audit_id']}.json"
            logger.debug(f"S3_AUDIT_PLACEHOLDER: Would write to S3 key: {s3_key}")
        await asyncio.sleep(0.001)

    def _perform_policy_checks(self, event_data: Dict[str, Any]) -> Optional[Dict[str, str]]:
        # For V0.1, this is a placeholder. Real checks would be implemented here.
        # Example structure for results: {"policy_name": "status (passed/failed/advisory)"}
        # results: Dict[str,str] = {} 
        # if event_data.get("event_type") == "SomeCriticalEvent":
        #     if "some_bad_pattern" in str(event_data):
        #         results["bad_pattern_check"] = "failed"
        #         self._publish_governance_alert("BadPatternDetected", "HIGH", "A bad pattern was detected.", event_data)
        return None # No checks implemented in V0.1

    def _check_slas_on_event(self, event_data: Dict[str, Any]):
        event_type = event_data.get("event_type")
        project_id = event_data.get("project_id")

        if event_type == "DagExecutionStatusEvent":
            dag_status_event = cast(DagExecutionStatusEvent, event_data) # Use cast for type checker
            if dag_status_event.get("status", "").startswith("COMPLETED"):
                dag_id = dag_status_event.get("dag_id", "unknown_dag")
                started_at_str = dag_status_event.get("started_at")
                completed_at_str = dag_status_event.get("completed_at")
                
                if started_at_str and completed_at_str:
                    try:
                        started = datetime.datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
                        completed = datetime.datetime.fromisoformat(completed_at_str.replace("Z", "+00:00"))
                        duration_seconds = (completed - started).total_seconds()
                        
                        logger.debug(f"DAG {dag_id} (Project: {project_id}) duration: {duration_seconds:.2f}s for SLA check.")
                        
                        sla_group = self.sla_definitions.get("dag_max_duration_seconds", {})
                        project_sla_duration_str = sla_group.get(project_id, sla_group.get("default")) if project_id else sla_group.get("default")

                        if project_sla_duration_str:
                            threshold = float(project_sla_duration_str)
                            if duration_seconds > threshold:
                                violation_details = (f"DAG '{dag_id}' in project '{project_id or 'N/A'}' exceeded SLA. "
                                                     f"Duration: {duration_seconds:.2f}s, Threshold: {threshold}s.")
                                self._publish_sla_violation(
                                    sla_name="dag_execution_time_limit", metric_name="dag_execution_duration_seconds",
                                    observed_value=duration_seconds, threshold_value=threshold,
                                    project_id=project_id, entity_id=dag_id, details=violation_details
                                )
                    except ValueError:
                        logger.error(f"Could not parse timestamps for DAG '{dag_id}' to check SLA.", exc_info=True)

    def _publish_sla_violation(self, sla_name: str, metric_name: str, observed_value: float, 
                               threshold_value: float, project_id: Optional[str], entity_id: Optional[str], details: str):
        if self.event_bus.redis_client:
            alert_id = str(uuid.uuid4())
            sla_event = SLAViolationEvent(
                event_type="SLAViolationEvent", alert_id=alert_id,
                timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
                sla_name=sla_name, metric_name=metric_name,
                observed_value=observed_value, threshold_value=threshold_value,
                project_id=project_id, entity_id=entity_id, details=details
            )
            self.event_bus.publish(GOVERNANCE_ALERTS_CHANNEL, sla_event)
            logger.warning(f"Published SLAViolationEvent ({alert_id}): {details}")

    def _publish_governance_alert(self, alert_type: str, severity: str, description: str, context_summary: Dict[str, Any]):
        if self.event_bus.redis_client:
            alert_id = str(uuid.uuid4())
            # Ensure context_summary is clean for JSON
            clean_context = {k: (str(v)[:200] if isinstance(v, (str, list, dict)) else v) for k,v in context_summary.items()}
            alert_event = GovernanceAlertEvent(
                event_type="GovernanceAlertEvent", alert_id=alert_id,
                timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
                alert_type=alert_type, severity=severity, description=description,
                context_summary=clean_context, recommendation="Further review required."
            )
            self.event_bus.publish(GOVERNANCE_ALERTS_CHANNEL, alert_event)
            logger.warning(f"Published GovernanceAlertEvent ({alert_id}) of type '{alert_type}': {description}")

    async def handle_event_for_governance(self, channel: str, event_data_str: str, parent_span_context: Optional[Any] = None):
        span = self._start_trace_span_if_available("handle_event", parent_context=parent_span_context, event_channel=channel)
        try:
            with span: # type: ignore
                logger.debug(f"GovernanceAgent processing event from channel '{channel}': {message_summary(event_data_str)}")
                event_data = json.loads(event_data_str)
                event_type = event_data.get("event_type", "UnknownEvent")

                if _tracer: span.set_attribute("event.type", event_type); span.set_attribute("event.project_id", event_data.get("project_id"))

                audit_entry = self._create_audit_log_entry(event_data, channel)
                await self._persist_audit_log(audit_entry)
                self._check_slas_on_event(event_data)
                # _perform_policy_checks is called within _create_audit_log_entry
                
                logger.debug(f"Finished governance processing for event '{event_type}' on channel '{channel}'.")
                if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
        except json.JSONDecodeError:
            logger.error(f"GovAgent: Could not decode JSON from '{channel}': {message_summary(event_data_str)}")
            if _tracer and _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "JSONDecodeError"))
        except Exception as e:
            logger.error(f"GovAgent: Error processing event from '{channel}': {e}", exc_info=True)
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Event processing failed"))

    async def main_event_loop(self):
        if not self.event_bus.redis_client or not AUDITABLE_EVENT_PATTERNS:
            logger.critical("GovernanceAgent: EventBus not connected or no audit patterns. Worker cannot start.")
            await asyncio.sleep(60)
            return

        pubsub = self.event_bus.redis_client.pubsub() # type: ignore # Corrected: self.event_bus.redis_client is already the client
        all_patterns_subscribed = True
        try:
            for pattern in AUDITABLE_EVENT_PATTERNS:
                await asyncio.to_thread(pubsub.psubscribe, pattern) # type: ignore
                logger.info(f"GovernanceAgent subscribed to pattern '{pattern}'")
        except redis.exceptions.RedisError as e: # type: ignore
            logger.critical(f"GovernanceAgent: Failed to psubscribe: {e}. Worker cannot start.")
            all_patterns_subscribed = False
        
        if not all_patterns_subscribed:
             logger.critical("GovernanceAgent: No subscriptions successful. Worker exiting.")
             if pubsub: await asyncio.to_thread(pubsub.close) # type: ignore
             return

        logger.info(f"GovernanceAgent worker started, listening for auditable events.")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0) # type: ignore
                if message and message["type"] == "pmessage":
                    # For pmessage, message['channel'] is the pattern, message['data'] is the actual channel
                    # No, for pmessage, message['channel'] is the pattern, message['data'] is the message content.
                    # message['pattern'] is the pattern that matched. message['channel'] is actual channel event came on.
                    actual_channel = message.get('channel')
                    if isinstance(actual_channel, bytes): actual_channel = actual_channel.decode('utf-8')
                    event_content = message.get('data')
                    if isinstance(event_content, bytes): event_content = event_content.decode('utf-8')
                    
                    if actual_channel and event_content:
                         span_name = "governance_agent.process_bus_event"
                         # For simplicity, not propagating trace context from Redis message itself in V0.1
                         # A more advanced setup might inject/extract W3C TraceContext into/from message payload.
                         parent_context_from_event = None 
                         if not self.tracer_instance:
                             await self.handle_event_for_governance(actual_channel, event_content)
                         else:
                            with self.tracer_instance.start_as_current_span(span_name, context=parent_context_from_event) as span:
                                span.set_attribute("messaging.source.channel", actual_channel)
                                await self.handle_event_for_governance(actual_channel, event_content, parent_span_context=None) # span is current context
                    else:
                        logger.warning(f"Received pmessage with no channel or data: {message}")

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
                        if pattern.strip(): await asyncio.to_thread(pubsub.punsubscribe, pattern.strip()) # type: ignore
                    await asyncio.to_thread(pubsub.close) # type: ignore
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
# In agents/GovernanceAgent/app/agent.py

# ... (other imports) ...
from interfaces.types.events import (
    # ... existing ...
    ProprietaryAuditEvent # <<< NEWLY IMPORTED
)

# Update AUDITABLE_EVENT_PATTERNS if needed, or ensure it's broad enough
# For direct subscription to the specific channel:
PRIVATE_GOVERNANCE_AUDIT_CHANNEL = "events.internal.governance.audit" 
# Add this to the list of channels/patterns the agent subscribes to in main_event_loop.
# If AUDITABLE_EVENT_PATTERNS already includes "events.internal.*", it might catch it.
# For clarity, let's assume we add it explicitly to the list it subscribes to.

# ... inside GovernanceAgent class ...

async def handle_proprietary_audit_event(self, event_data: ProprietaryAuditEvent, channel: str):
    span = self._start_trace_span_if_available("handle_proprietary_audit_event", 
                                               audit_id=event_data.get("audit_id"), 
                                               source_service=event_data.get("source_service"))
    try:
        with span: # type: ignore
            logger.info(f"Received ProprietaryAuditEvent from channel '{channel}': ID {event_data.get('audit_id')}")

            # Create a standard AuditLogEntry from this private event for our system's audit trail
            # This shows how data from private stack is integrated into public audit trail
            audit_entry = AuditLogEntry(
                event_type="AuditEvent", # Our standard audit event type
                audit_id=str(uuid.uuid4()), # New ID for this specific log entry in our system
                timestamp=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
                source_event_type=event_data.get("event_type", "ProprietaryAuditEvent"),
                source_event_id=event_data.get("audit_id"),
                service_name=event_data.get("source_service", "PrivateIntelligenceStack"),
                project_id=event_data.get("data_payload", {}).get("project_id"), # If available in payload
                commit_sha=event_data.get("data_payload", {}).get("commit_sha"), # If available
                user_or_actor=event_data.get("actor", "MCP/GovernanceBridge"),
                action_description=f"Received audit from private stack: {event_data.get('action', 'No action specified')}",
                event_details={ # Store the whole private event as details
                    "private_audit_id": event_data.get("audit_id"),
                    "private_timestamp": event_data.get("timestamp"),
                    "private_source_service": event_data.get("source_service"),
                    "private_action": event_data.get("action"),
                    "private_payload": event_data.get("data_payload"),
                    "private_signature": event_data.get("signature")
                },
                policy_check_results=None, # This agent might run its own checks
                severity=event_data.get("metadata", {}).get("severity", "INFO") # If private event has severity
            )
            await self._persist_audit_log(audit_entry)
            if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
    except Exception as e:
        logger.error(f"Error handling ProprietaryAuditEvent: {e}", exc_info=True)
        if _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR))


async def _route_event(self, channel: str, event_data_str: str, parent_span: Optional[Any] = None):
    # ... (existing _route_event logic from response #73 or #77) ...
    # Add a case for the new event type:
    # event_data = json.loads(event_data_str)
    # event_type = event_data.get("event_type")
    # ...
    # elif event_type == "ProprietaryAuditEvent": # The type string used by your private bridge
    #     if all(k in event_data for k in ["audit_id", "timestamp", "source_service", "action", "data_payload"]):
    #         await self.handle_proprietary_audit_event(ProprietaryAuditEvent(**event_data), channel) #type: ignore
    #     else: 
    #         logger.error(f"Malformed ProprietaryAuditEvent data for routing: {event_data_str[:200]}")
    # ...
    # A more generic way if the channel itself is specific:
    if channel == PRIVATE_GOVERNANCE_AUDIT_CHANNEL: # If GovernanceAgent subscribes to this specific channel
        try:
            event_data_dict = json.loads(event_data_str)
            # Assuming the payload itself matches ProprietaryAuditEvent structure
            await self.handle_proprietary_audit_event(event_data_dict, channel) #type: ignore
        except json.JSONDecodeError:
            logger.error(f"Could not decode JSON for ProprietaryAuditEvent from '{channel}'")
        return # End routing for this specific channel

    # ... (rest of existing _route_event logic for other event types) ...


async def main_event_loop(self):
    # ...
    # In main_event_loop, add PRIVATE_GOVERNANCE_AUDIT_CHANNEL to the subscription list
    # if using specific channel subscription instead of just patterns in AUDITABLE_EVENT_PATTERNS
    # If AUDITABLE_EVENT_PATTERNS covers "events.internal.governance.audit", no change needed to subscription list.
    # For explicit subscription:
    # try:
    #     await asyncio.to_thread(pubsub.subscribe, PRIVATE_GOVERNANCE_AUDIT_CHANNEL)
    #     logger.info(f"GovernanceAgent subscribed to specific channel '{PRIVATE_GOVERNANCE_AUDIT_CHANNEL}'")
    # except ...
    # ...
    # And ensure punsubscribe/unsubscribe handles it in `finally`.
    # For V0.1, having AUDITABLE_EVENT_PATTERNS = ["events.internal.governance.*", ...other patterns...] is simplest.
    # Then the existing psubscribe loop will pick it up.
    # The current AUDITABLE_EVENT_PATTERNS includes "events.system.*", so if you publish to
    # "events.system.private_governance.audit", it would be caught. Let's refine it.

    # Refined subscription in main_event_loop:
    current_patterns_to_subscribe = AUDITABLE_EVENT_PATTERNS + [PRIVATE_GOVERNANCE_AUDIT_CHANNEL] # Add specific channel if not covered by patterns
    # ... then use current_patterns_to_subscribe in the psubscribe/subscribe loop ...
    # Actually, pubsub.psubscribe handles patterns. If PRIVATE_GOVERNANCE_AUDIT_CHANNEL is specific,
    # you'd use pubsub.subscribe for it, or ensure AUDITABLE_EVENT_PATTERNS includes a pattern that matches it.
    # Let's assume AUDITABLE_EVENT_PATTERNS = ["events.project.*", "events.system.*", "events.agent.*.lifecycle", "events.internal.governance.*"]
    # ... (rest of main_event_loop)
