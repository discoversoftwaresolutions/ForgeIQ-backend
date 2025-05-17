# ========================================
# 📁 agents/ReportingAgent/app/agent.py (V0.2)
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
_tracer = None
_trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing
    _tracer = setup_tracing(SERVICE_NAME)
    _trace_api = otel_trace_api
except ImportError:
    logging.getLogger(SERVICE_NAME).warning("ReportingAgent: Tracing setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
from interfaces.types.events import * # Import all known event types

# Configuration for Sinks
REPORTING_SINK_S3_ENABLED = os.getenv("REPORTING_SINK_S3_ENABLED", "false").lower() == "true"
REPORTING_S3_BUCKET_AUDIT = os.getenv("REPORTING_S3_BUCKET_AUDIT")
REPORTING_S3_KEY_PREFIX = os.getenv("REPORTING_S3_KEY_PREFIX", "system-events").strip("/")

REPORTING_SINK_PROMETHEUS_ENABLED = os.getenv("REPORTING_SINK_PROMETHEUS_ENABLED", "false").lower() == "true"
PROMETHEUS_PUSHGATEWAY_URL = os.getenv("PROMETHEUS_PUSHGATEWAY_URL")


EVENT_CHANNELS_TO_MONITOR_STR = os.getenv(
    "REPORTING_EVENT_PATTERNS",
    "events.project.*.dag.*,events.project.*.test.failures,events.project.*.patch.*,"
    "events.project.*.security.scan_result,events.project.*.deployment.status,"
    "events.codenav.*,events.system.governance.alerts,events.agent.*.lifecycle"
)
EVENT_CHANNELS_TO_MONITOR = [p.strip() for p in EVENT_CHANNELS_TO_MONITOR_STR.split(',') if p.strip()]

# Boto3 S3 client (initialized if S3 sink is enabled)
s3_client = None
if REPORTING_SINK_S3_ENABLED:
    if not REPORTING_S3_BUCKET_AUDIT:
        logger.error("S3 Sink is enabled but REPORTING_S3_BUCKET_AUDIT is not set. S3 uploads will fail.")
    else:
        try:
            import boto3
            # Ensure AWS credentials are configured in the environment:
            # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
            # Or an IAM role if running on AWS infrastructure or Railway with IAM integration.
            s3_client = boto3.client("s3")
            logger.info(f"S3 sink enabled. Will upload reports to bucket: {REPORTING_S3_BUCKET_AUDIT}")
        except ImportError:
            logger.error("boto3 library not found, but S3 sink is enabled. S3 uploads will fail. Please pip install boto3.")
            REPORTING_SINK_S3_ENABLED = False # Disable if library missing
        except Exception as e:
            logger.error(f"Failed to initialize Boto3 S3 client: {e}", exc_info=True)
            REPORTING_SINK_S3_ENABLED = False

# Prometheus client (initialized if Prometheus sink is enabled)
prometheus_registry = None
event_counter_metric = None
if REPORTING_SINK_PROMETHEUS_ENABLED:
    if not PROMETHEUS_PUSHGATEWAY_URL:
        logger.warning("Prometheus sink enabled but PROMETHEUS_PUSHGATEWAY_URL not set. Metrics might not be pushed.")
    try:
        from prometheus_client import CollectorRegistry, Counter, push_to_gateway
        prometheus_registry = CollectorRegistry()
        event_counter_metric = Counter(
            "forgeiq_agent_events_processed_total",
            "Total number of events processed by the ReportingAgent",
            ["event_type", "project_id", "status_category"], # Example labels
            registry=prometheus_registry
        )
        logger.info("Prometheus metrics sink enabled.")
    except ImportError:
        logger.error("prometheus_client library not found, but Prometheus sink is enabled. Metrics will not be pushed. Please pip install prometheus_client.")
        REPORTING_SINK_PROMETHEUS_ENABLED = False


class ReportingAgent:
    def __init__(self):
        logger.info("Initializing ReportingAgent (V0.2 with S3 & Prometheus conceptual sinks)...")
        self.event_bus = EventBus()
        if not self.event_bus.redis_client:
            logger.error("ReportingAgent critical: EventBus not connected.")
        logger.info("ReportingAgent V0.2 Initialized.")

    @property
    def tracer_instance(self):
        return _tracer

    def _start_trace_span_if_available(self, operation_name: str, parent_context: Optional[Any] = None, **attrs):
        # ... (same helper as before) ...
        if self.tracer_instance and _trace_api:
            span = self.tracer_instance.start_span(f"reporting_agent.{operation_name}", context=parent_context)
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

    def _format_report_for_audit(self, event_type: str, event_data: Dict[str, Any], received_channel: str) -> Dict[str, Any]:
        """Formats an event into a structured report object for audit."""
        # Similar to GovernanceAgent's AuditLogEntry but can be tailored for ReportingAgent
        report_id = str(uuid.uuid4())
        timestamp = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"

        # Basic sanitization/summarization of event_data for logging
        payload_summary = {
            k: (str(v)[:200] + '...' if isinstance(v, str) and len(v) > 200 else v)
            for k, v in event_data.items()
            if isinstance(v, (str, int, float, bool)) # Only simple types for summary
        }
        if len(payload_summary) > 10: # Limit number of keys in summary
            payload_summary = dict(list(payload_summary.items())[:10])
            payload_summary["..."] = "payload_truncated_for_summary"


        return {
            "reportId": report_id,
            "ingestedAt": timestamp,
            "sourceEvent": {
                "type": event_type,
                "channel": received_channel,
                "projectId": event_data.get("project_id"),
                "commitSha": event_data.get("commit_sha"),
                "eventId": event_data.get("event_id") or event_data.get("request_id") or event_data.get("dag_id"),
                "payloadSummary": payload_summary 
                # For full audit, include the entire 'event_data' after careful sanitization
                # "fullEventPayload": event_data 
            },
            "reportedBy": SERVICE_NAME,
            "reportVersion": "0.2.0"
        }

    async def _send_report_to_s3(self, report_data: Dict[str, Any]):
        if not REPORTING_SINK_S3_ENABLED or not s3_client or not REPORTING_S3_BUCKET_AUDIT:
            if REPORTING_SINK_S3_ENABLED: # Log only if it was intended to run
                logger.debug("S3 sink not pushing: S3 not configured or boto3 client failed.")
            return

        span = self._start_trace_span_if_available("send_to_s3", s3_bucket=REPORTING_S3_BUCKET_AUDIT)
        try:
            with span: # type: ignore
                event_type = report_data.get("sourceEvent", {}).get("type", "unknown_event")
                project_id_path = "".join(c if c.isalnum() else '_' for c in report_data.get("sourceEvent", {}).get("projectId", "system"))
                ts_obj = datetime.datetime.fromisoformat(report_data["ingestedAt"].replace("Z","+00:00"))
                date_path = ts_obj.strftime('%Y/%m/%d/%H') # Partition by hour

                s3_key_name = f"{report_data['reportId']}.json"
                s3_full_key = f"{REPORTING_S3_KEY_PREFIX}/{project_id_path}/{event_type}/{date_path}/{s3_key_name}"

                logger.debug(f"Attempting to upload report {report_data['reportId']} to S3: s3://{REPORTING_S3_BUCKET_AUDIT}/{s3_full_key}")

                # Boto3 S3 client calls are synchronous, run in a thread for async context
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, # Default ThreadPoolExecutor
                    s3_client.put_object, # type: ignore
                    Bucket=REPORTING_S3_BUCKET_AUDIT,
                    Key=s3_full_key,
                    Body=json.dumps(report_data, indent=2), # Pretty print for S3
                    ContentType='application/json'
                )
                logger.info(f"Successfully uploaded report {report_data['reportId']} to S3: {s3_full_key}")
                if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))

        except AttributeError as ae: # If s3_client is None
            logger.error(f"S3 client not available for report {report_data.get('reportId')}: {ae}")
            if _trace_api and span: span.record_exception(ae); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "S3 client not available"))
        except Exception as e:
            logger.error(f"Failed to push report {report_data.get('reportId')} to S3: {e}", exc_info=True)
            if _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "S3 upload failed"))

    async def _push_event_metric(self, event_type: str, project_id: Optional[str], status_category: Optional[str] = "processed"):
        if not REPORTING_SINK_PROMETHEUS_ENABLED or not event_counter_metric or not PROMETHEUS_PUSHGATEWAY_URL:
            if REPORTING_SINK_PROMETHEUS_ENABLED:
                logger.debug("Prometheus sink not pushing: not configured or client failed.")
            return

        span = self._start_trace_span_if_available("push_event_metric", metric_name="forgeiq_agent_events_processed_total")
        try:
            with span: #type: ignore
                labels = {
                    "event_type": event_type or "unknown",
                    "project_id": project_id or "none",
                    "status_category": status_category or "unknown"
                }
                event_counter_metric.labels(**labels).inc() # type: ignore

                # Pushing to gateway is a synchronous blocking call
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    push_to_gateway, # type: ignore
                    PROMETHEUS_PUSHGATEWAY_URL,
                    job=SERVICE_NAME,
                    registry=prometheus_registry # type: ignore
                )
                logger.debug(f"Pushed Prometheus event_counter metric for event_type '{event_type}'")
                if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
        except NameError: # prometheus_client or its components not imported
            logger.error("Prometheus client library not available for pushing metrics.")
            if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Prometheus client lib missing"))
        except Exception as e:
            logger.error(f"Failed to push Prometheus metric for event_type '{event_type}': {e}", exc_info=True)
            if _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Prometheus push failed"))


    async def handle_event_for_reporting(self, channel: str, event_data_str: str, parent_span_context: Optional[Any] = None):
        # (Tracing setup similar to GovernanceAgent's handle_event_for_governance)
        span = self._start_trace_span_if_available("handle_event_for_reporting", parent_context=parent_span_context, event_channel=channel)
        try:
            with span: #type: ignore
                logger.debug(f"ReportingAgent received raw message on channel '{channel}': {message_summary(event_data_str)}")
                event_data = json.loads(event_data_str)
                event_type = event_data.get("event_type", "UnknownEvent")

                if _tracer: span.set_attribute("event.type", event_type); span.set_attribute("event.project_id", event_data.get("project_id"))

                # 1. Format a standard report/audit log entry
                report_entry = self._format_report_for_audit(event_type, event_data, channel)

                # 2. Log it (primary V0.1 sink)
                logger.info(f"REPORT_DATA: {json.dumps(report_entry)}")

                # 3. Push to S3 if enabled
                if REPORTING_SINK_S3_ENABLED:
                    await self._send_report_to_s3(report_entry) # report_entry is already dict

                # 4. Push basic metrics if enabled
                if REPORTING_SINK_PROMETHEUS_ENABLED:
                    status_category = "processed"
                    if "status" in event_data: # For status events
                        status_val = str(event_data["status"]).lower()
                        if "fail" in status_val or "error" in status_val: status_category = "failure"
                        elif "success" in status_val or "completed_clean" in status_val : status_category = "success"
                    await self._push_event_metric(event_type, event_data.get("project_id"), status_category)

                if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
        except json.JSONDecodeError:
            logger.error(f"ReportingAgent: Could not decode JSON from '{channel}': {message_summary(event_data_str)}")
            if _tracer and _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "JSONDecodeError"))
        except Exception as e:
            logger.error(f"ReportingAgent: Error processing event from '{channel}': {e}", exc_info=True)
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Event processing error"))


    async def main_event_loop(self):
        # (Same event loop structure as GovernanceAgent, subscribing to EVENT_CHANNELS_TO_MONITOR)
        if not self.event_bus.redis_client or not EVENT_CHANNELS_TO_MONITOR:
            logger.critical("ReportingAgent: EventBus not connected or no monitor patterns. Worker cannot start."); await asyncio.sleep(60); return
        pubsub = self.event_bus.redis_client.pubsub() #type: ignore
        try:
            for pattern in EVENT_CHANNELS_TO_MONITOR: await asyncio.to_thread(pubsub.psubscribe, pattern); logger.info(f"ReportingAgent subscribed to '{pattern}'") #type: ignore
        except redis.exceptions.RedisError as e: logger.critical(f"ReportingAgent: Failed to psubscribe: {e}. Exiting."); return #type: ignore
        logger.info(f"ReportingAgent worker (V0.2) started, listening...")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0) #type: ignore
                if message and message["type"] == "pmessage":
                    actual_channel = message.get('channel'); event_content = message.get('data')
                    if isinstance(actual_channel, bytes): actual_channel = actual_channel.decode('utf-8', errors='replace')
                    if isinstance(event_content, bytes): event_content = event_content.decode('utf-8', errors='replace')
                    if actual_channel and event_content:
                        await self.handle_event_for_reporting(actual_channel, event_content)
                await asyncio.sleep(0.01)
        except KeyboardInterrupt: logger.info("ReportingAgent event loop interrupted.")
        except Exception as e: logger.error(f"Critical error in ReportingAgent event loop: {e}", exc_info=True)
        finally: # ... (pubsub cleanup as in GovernanceAgent)
            logger.info("ReportingAgent shutting down...");
            if pubsub:
                try: 
                    for p in EVENT_CHANNELS_TO_MONITOR: 
                        if p.strip(): await asyncio.to_thread(pubsub.punsubscribe, p.strip()) #type: ignore
                    await asyncio.to_thread(pubsub.close) #type: ignore
                except: pass # Ignore errors during cleanup
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
        logger.critical(f"ReportingAgent failed to start: {e}", exc_info=True)
