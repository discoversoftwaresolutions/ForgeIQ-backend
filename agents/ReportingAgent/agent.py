# ========================================
# 📁 agents/ReportingAgent/agent.py
# ========================================
import os
import json
import datetime
import asyncio
import logging
import uuid # For generating report IDs if needed
from typing import Dict, Any, Optional, List

# --- Observability Setup ---
SERVICE_NAME = "ReportingAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(otelTraceID)s - %(otelSpanID)s - %(message)s'
)
tracer = None
try:
    from opentelemetry import trace
    from core.observability.tracing import setup_tracing
    tracer = setup_tracing(SERVICE_NAME)
except ImportError:
    logging.getLogger(SERVICE_NAME).warning("ReportingAgent: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from interfaces.types.events import * # Import all known event types

# Configuration for Sinks
REPORTING_S3_BUCKET_AUDIT = os.getenv("REPORTING_S3_BUCKET_AUDIT")
# REPORTING_METRICS_PUSHGATEWAY = os.getenv("REPORTING_METRICS_PUSHGATEWAY") # For Prometheus
# REPORTING_ELK_INDEX_EVENTS = os.getenv("REPORTING_ELK_INDEX_EVENTS")

EVENT_CHANNELS_TO_MONITOR = [
    "events.project.*.dag.*",          
    "events.project.*.test.failures",  
    "events.project.*.patch.*",        
    "events.project.*.security.scan_result",
    "events.project.*.deployment.status",
    "events.codenav.*",                
    "events.system.governance.alerts", # Listen to alerts from GovernanceAgent
    # Add other patterns for events of interest
]

class ReportingAgent:
    def __init__(self):
        logger.info("Initializing ReportingAgent (V0.2)...")
        self.event_bus = EventBus()
        if not self.event_bus.redis_client:
            logger.error("ReportingAgent critical: EventBus not connected.")
        logger.info("ReportingAgent V0.2 Initialized.")

    @property
    def tracer_instance(self):
        return tracer

    # --- Placeholder Sink Methods ---
    async def _send_to_s3_audit(self, report_data: Dict[str, Any], event_type: str, project_id: Optional[str]):
        if not REPORTING_S3_BUCKET_AUDIT:
            logger.debug("S3 audit sink not configured. Skipping S3 push.")
            return

        timestamp_iso = report_data.get("timestamp", datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z")
        date_partition = datetime.datetime.fromisoformat(timestamp_iso.replace("Z","+00:00")).strftime('%Y/%m/%d')
        report_id = report_data.get("audit_id") or report_data.get("event_id") or report_data.get("request_id") or str(uuid.uuid4())

        s3_key = f"audit_trail/{project_id or 'system'}/{event_type}/{date_partition}/{report_id}.json"
        logger.info(f"S3_AUDIT_PLACEHOLDER: Would push report for event type '{event_type}' to s3://{REPORTING_S3_BUCKET_AUDIT}/{s3_key}")
        # In real implementation:
        # try:
        #     import boto3
        #     s3 = boto3.client('s3') # Ensure credentials/roles are configured in the environment
        #     s3.put_object(Bucket=REPORTING_S3_BUCKET_AUDIT, Key=s3_key, Body=json.dumps(report_data, indent=2), ContentType='application/json')
        #     logger.info(f"Successfully pushed audit log to S3: {s3_key}")
        # except Exception as e:
        #     logger.error(f"Failed to push audit log to S3 ({s3_key}): {e}", exc_info=True)
        await asyncio.sleep(0.01) # Simulate async I/O

    async def _push_metric_placeholder(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None):
        # Placeholder for pushing to Prometheus Pushgateway or similar
        # from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
        # registry = CollectorRegistry()
        # g = Gauge(metric_name, 'Description of metric', list(tags.keys()) if tags else [], registry=registry)
        # if tags: g.labels(**tags).set(value)
        # else: g.set(value)
        # if REPORTING_METRICS_PUSHGATEWAY:
        #    push_to_gateway(REPORTING_METRICS_PUSHGATEWAY, job=SERVICE_NAME, registry=registry)
        #    logger.info(f"METRIC_PLACEHOLDER: Pushed metric '{metric_name}' with value {value} and tags {tags}")
        logger.debug(f"METRIC_PLACEHOLDER: Metric '{metric_name}' = {value}, Tags = {tags}")
        await asyncio.sleep(0.01) # Simulate async I/O


    # --- Specific Event Handlers ---
    async def handle_dag_execution_status(self, event_data: DagExecutionStatusEvent, channel: str):
        logger.info(f"Reporting: DAG Execution Status for DAG {event_data['dag_id']} is {event_data['status']}")
        await self._send_to_s3_audit(event_data, "DagExecutionStatus", event_data.get("project_id"))

        # Example Metric
        if event_data.get("started_at") and event_data.get("completed_at"):
            try:
                start = datetime.datetime.fromisoformat(event_data["started_at"].replace("Z", "+00:00"))
                end = datetime.datetime.fromisoformat(event_data["completed_at"].replace("Z", "+00:00"))
                duration = (end - start).total_seconds()
                await self._push_metric_placeholder(
                    "dag_execution_duration_seconds", 
                    duration, 
                    {"project_id": event_data.get("project_id","unknown"), "dag_id": event_data["dag_id"], "status": event_data["status"]}
                )
            except ValueError:
                logger.warning(f"Could not parse timestamps for DAG duration metric: {event_data['dag_id']}")

        if event_data["status"] == "FAILED":
             await self._push_metric_placeholder("dag_execution_failures_total", 1, {"project_id": event_data.get("project_id","unknown"), "dag_id": event_data["dag_id"]})
        elif event_data["status"] == "COMPLETED_SUCCESS":
             await self._push_metric_placeholder("dag_execution_success_total", 1, {"project_id": event_data.get("project_id","unknown"), "dag_id": event_data["dag_id"]})


    async def handle_task_status_update(self, event_data: TaskStatusUpdateEvent, channel: str):
        task_info = event_data["task"]
        logger.info(f"Reporting: Task Status Update for DAG {event_data['dag_id']}, Task {task_info['task_id']} is {task_info['status']}")
        await self._send_to_s3_audit(event_data, "TaskStatusUpdate", event_data.get("project_id"))

        if task_info["status"] == "FAILED":
            await self._push_metric_placeholder(
                "task_execution_failures_total", 1, 
                {"project_id": event_data.get("project_id","unknown"), "dag_id": event_data["dag_id"], "task_id": task_info["task_id"]}
            )
        elif task_info["status"] == "SUCCESS": # Note: PlanAgent used 'SUCCESS' not 'COMPLETED_SUCCESS' for tasks
            await self._push_metric_placeholder(
                "task_execution_success_total", 1,
                {"project_id": event_data.get("project_id","unknown"), "dag_id": event_data["dag_id"], "task_id": task_info["task_id"]}
            )


    async def handle_security_scan_result(self, event_data: SecurityScanResultEvent, channel: str):
        logger.info(f"Reporting: Security Scan Result for project {event_data['project_id']}, type {event_data['scan_type']}, findings: {len(event_data['findings'])}")
        await self._send_to_s3_audit(event_data, "SecurityScanResult", event_data.get("project_id"))
        await self._push_metric_placeholder(
            "security_findings_total", 
            len(event_data['findings']),
            {"project_id": event_data.get("project_id","unknown"), "scan_type": event_data['scan_type'], "tool_name": event_data['tool_name']}
        )

    async def handle_generic_auditable_event(self, event_type: str, event_data: Dict[str, Any], channel: str):
        """Handles any other event by creating a generic audit log."""
        logger.info(f"Reporting: Generic auditable event '{event_type}' on channel '{channel}'.")
        # Use a simplified version of GovernanceAgent's audit log for now
        audit_payload = {
            "audit_timestamp": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            "original_event_type": event_type,
            "original_channel": channel,
            "original_payload_summary": message_summary(event_data, 200),
            # For full audit, store the whole event_data
        }
        await self._send_to_s3_audit(audit_payload, f"GenericAudit_{event_type}", event_data.get("project_id"))


    async def process_event_from_bus(self, channel: str, event_data_str: str):
        """Top-level router for incoming events from the bus."""
        span_name = "reporting_agent.process_event"
        parent_context = None # Extract from event_data_str if trace context is propagated

        if not self.tracer_instance: # Fallback
            await self._process_event_from_bus_logic(channel, event_data_str)
            return

        with self.tracer_instance.start_as_current_span(span_name, context=parent_context) as span:
            span.set_attributes({
                "messaging.system": "redis",
                "messaging.destination.name": channel, # Actual channel from pmessage
                "messaging.operation": "process"
            })
            try:
                await self._process_event_from_bus_logic(channel, event_data_str, span)
            except Exception as e:
                logger.error(f"Unhandled error in _process_event_from_bus_logic from {channel}: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Event processing logic error"))

    async def _process_event_from_bus_logic(self, channel: str, event_data_str: str, span: Optional[trace.Span] = None):
        try:
            event_data = json.loads(event_data_str)
            event_type = event_data.get("event_type")

            if span:
                span.set_attribute("event.type", event_type or "unknown")
                span.set_attribute("event.project_id", event_data.get("project_id"))

            # Specific handlers
            if event_type == "DagExecutionStatusEvent":
                # Validate structure before casting, Pydantic would be better
                if all(k in event_data for k in ["dag_id", "status", "started_at", "task_statuses"]):
                    await self.handle_dag_execution_status(DagExecutionStatusEvent(**event_data), channel)
                else: logger.error(f"Malformed DagExecutionStatusEvent: {message_summary(event_data)}")

            elif event_type == "TaskStatusUpdateEvent":
                if all(k in event_data for k in ["dag_id", "task"]):
                    await self.handle_task_status_update(TaskStatusUpdateEvent(**event_data), channel)
                else: logger.error(f"Malformed TaskStatusUpdateEvent: {message_summary(event_data)}")

            elif event_type == "SecurityScanResultEvent":
                if all(k in event_data for k in ["project_id", "scan_type", "tool_name", "status", "findings"]):
                    await self.handle_security_scan_result(SecurityScanResultEvent(**event_data), channel)
                else: logger.error(f"Malformed SecurityScanResultEvent: {message_summary(event_data)}")

            # Add more specific handlers here for other important events
            # elif event_type == "TestFailedEvent":
            #     await self.handle_test_failure(TestFailedEvent(**event_data), channel)
            # elif event_type == "PatchSuggestedEvent":
            #     await self.handle_patch_suggestion(PatchSuggestedEvent(**event_data), channel)

            else: # Generic handler for other events for auditing
                await self.handle_generic_auditable_event(event_type or "UnknownEventType", event_data, channel)

        except json.JSONDecodeError:
            logger.error(f"ReportingAgent: Could not decode JSON from event on channel '{channel}': {message_summary(event_data_str)}")
            if span and trace_api: span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "JSONDecodeError"))
        except TypeError as te: # For TypedDict unpacking
            logger.error(f"ReportingAgent: TypeError processing event from '{channel}' (mismatched keys?): {te} - Data: {message_summary(event_data_str)}")
            if span and trace_api: span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "TypeError processing event"))
        except Exception as e:
            logger.error(f"ReportingAgent: Error processing event from channel '{channel}': {e}", exc_info=True)
            if span and trace_api: span.record_exception(e); span.set_status(trace.Status(trace_api.StatusCode.ERROR, "Generic event processing error"))


    async def main_event_loop(self):
        if not self.event_bus.redis_client:
            logger.critical("ReportingAgent: EventBus not connected. Worker cannot start.")
            await asyncio.sleep(60)
            return

        pubsub = self.event_bus.redis_client.pubsub()
        subscribed_ok = False
        try:
            for pattern in EVENT_CHANNELS_TO_MONITOR:
                await asyncio.to_thread(pubsub.psubscribe, pattern)
                logger.info(f"ReportingAgent subscribed to pattern '{pattern}'")
            subscribed_ok = True
        except redis.exceptions.RedisError as e:
            logger.critical(f"ReportingAgent: Failed to psubscribe: {e}. Worker cannot start.")
            return

        if not subscribed_ok:
             logger.critical("ReportingAgent: No subscriptions successful. Worker exiting.")
             return

        logger.info(f"ReportingAgent worker started, listening for events on patterns: {EVENT_CHANNELS_TO_MONITOR}")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "pmessage":
                    await self.process_event_from_bus(message["channel"], message["data"])
                await asyncio.sleep(0.01) 
        except KeyboardInterrupt:
            logger.info("ReportingAgent event loop interrupted.")
        except Exception as e:
            logger.error(f"Critical error in ReportingAgent event loop: {e}", exc_info=True)
        finally:
            logger.info("ReportingAgent shutting down pubsub...")
            if pubsub:
                try: 
                    for pattern in EVENT_CHANNELS_TO_MONITOR:
                       await asyncio.to_thread(pubsub.punsubscribe, pattern)
                    await asyncio.to_thread(pubsub.close)
                except Exception as e_close:
                     logger.error(f"Error closing pubsub for ReportingAgent: {e_close}")
            logger.info("ReportingAgent shutdown complete.")

async def main_async_runner():
    agent = ReportingAgent()
    await agent.main_event_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main_async_runner())
    except KeyboardInterrupt:
        logger.info("ReportingAgent main execution stopped by user.")
    except Exception as e:
        logger.critical(f"ReportingAgent failed to start or unhandled error in __main__: {e}", exc_info=True)
