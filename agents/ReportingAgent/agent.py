# ========================================
# 📁 agents/ReportingAgent/app/agent.py
# ========================================
import os
import json
import datetime
import asyncio
import logging
from typing import Dict, Any, Optional

# --- Observability Setup ---
SERVICE_NAME = "ReportingAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s'
)
tracer = None
try:
    from opentelemetry import trace # For span context
    from core.observability.tracing import setup_tracing
    tracer = setup_tracing(SERVICE_NAME)
except ImportError:
    logging.getLogger(SERVICE_NAME).warning(
        "ReportingAgent: Tracing setup failed. Ensure core.observability.tracing is available."
    )
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus, message_summary
# We'll import all known event types to potentially handle them
from interfaces.types.events import * # This imports all TypedDicts from events.py

# Configuration
# For V0.1, we'll mostly log. Future versions would use these for actual pushing.
REPORTING_S3_BUCKET = os.getenv("REPORTING_S3_BUCKET")
REPORTING_ELK_ENDPOINT = os.getenv("REPORTING_ELK_ENDPOINT")
DEFAULT_SINK = os.getenv("REPORTING_DEFAULT_SINK", "console_log") # console_log, s3, elk

# Define channels/patterns to listen to. This agent might listen to many things.
# Using psubscribe (pattern subscribe) for broad categories.
EVENT_CHANNELS_PATTERNS = [
    "events.project.*.dag.*",          # All DAG related events (created, execution status, task status)
    "events.project.*.test.failures",  # Test failures
    "events.project.*.patch.*",        # Patch suggestions/status
    "events.codenav.*",                # Events from CodeNavAgent (e.g., indexing status)
    "events.system.errors",            # A conceptual channel for system-wide critical errors
    "events.agent.*.lifecycle"         # Conceptual: agent startup/shutdown events
]

class ReportingAgent:
    def __init__(self):
        logger.info("Initializing ReportingAgent...")
        self.event_bus = EventBus()
        if not self.event_bus.redis_client:
            logger.error("ReportingAgent critical: EventBus not connected. Cannot receive events.")
        logger.info(f"ReportingAgent Initialized. Default sink: {DEFAULT_SINK}")

    @property
    def tracer_instance(self):
        return tracer

    def format_report(self, event_type: str, event_data: Dict[str, Any], received_channel: str) -> Dict[str, Any]:
        """Formats an event into a structured report object."""
        return {
            "report_id": str(uuid.uuid4()),
            "received_at": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            "source_event_type": event_type,
            "source_channel": received_channel,
            "event_payload": event_data,
            "reporting_agent_version": "0.1.0", # Example version for this agent
            "processing_node": os.getenv("HOSTNAME", "unknown_node") # Kubernetes pod name or similar
        }

    async def push_to_sink(self, report: Dict[str, Any]):
        """
        Pushes the formatted report to the configured sink(s).
        For V0.1, this will primarily be structured logging.
        Future: S3, ELK, Metrics store etc.
        """
        span_name = "reporting_agent.push_to_sink"
        if not self.tracer_instance: # Fallback
            self._push_to_sink_logic(report)
            return

        with self.tracer_instance.start_as_current_span(span_name) as span:
            span.set_attribute("reporting.sink_target", DEFAULT_SINK)
            span.set_attribute("reporting.event_type", report.get("source_event_type", "unknown"))
            try:
                self._push_to_sink_logic(report)
                span.set_status(trace.StatusCode.OK)
            except Exception as e:
                logger.error(f"Error pushing report to sink: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, f"Push to sink failed: {e}"))


    def _push_to_sink_logic(self, report: Dict[str, Any]):
        report_summary = message_summary(report.get("event_payload", {}), 200)
        logger.info(f"REPORT Event: {report.get('source_event_type')} from {report.get('source_channel')}. Summary: {report_summary}")

        # In a production system, you'd log the full structured report as JSON for easy parsing
        # For console, a summary might be fine, but structured JSON is better for log collectors.
        # logger.info(json.dumps(report)) # This would be for machine consumption

        if DEFAULT_SINK == "s3_placeholder" and REPORTING_S3_BUCKET:
            # Placeholder for S3 logic
            # s3_key = f"reports/{report.get('source_event_type')}/{report.get('received_at', '').split('T')[0]}/{report.get('report_id')}.json"
            # logger.info(f"SINK_S3_PLACEHOLDER: Would push to s3://{REPORTING_S3_BUCKET}/{s3_key}")
            # try:
            #     # import boto3
            #     # s3 = boto3.client('s3')
            #     # s3.put_object(Bucket=REPORTING_S3_BUCKET, Key=s3_key, Body=json.dumps(report))
            # except Exception as e:
            #     logger.error(f"Failed to push report to S3 placeholder: {e}")
            pass # No actual S3 push in this V0.1

        elif DEFAULT_SINK == "elk_placeholder" and REPORTING_ELK_ENDPOINT:
            # Placeholder for ELK logic
            # logger.info(f"SINK_ELK_PLACEHOLDER: Would push to {REPORTING_ELK_ENDPOINT}")
            # try:
            #     # async with httpx.AsyncClient() as client: # If this method were async
            #     #    await client.post(REPORTING_ELK_ENDPOINT, json=report)
            # except Exception as e:
            #     logger.error(f"Failed to push report to ELK placeholder: {e}")
            pass # No actual ELK push in this V0.1

        # Future: Add Prometheus metrics pushing here
        # from prometheus_client import Counter, CollectorRegistry, push_to_gateway
        # c = Counter(f"event_{report.get('source_event_type', 'unknown').lower()}_total", "Count of events processed by reporting agent")
        # c.inc()
        # push_to_gateway(...)


    async def handle_event(self, channel: str, event_data_str: str):
        span_name = "reporting_agent.handle_event"
        # Attempt to extract parent trace context if propagated in event_data_str or headers (not shown here)
        parent_context = None 

        if not self.tracer_instance: # Fallback
            await self._handle_event_logic(channel, event_data_str)
            return

        with self.tracer_instance.start_as_current_span(span_name, context=parent_context) as span:
            span.set_attributes({
                "messaging.system": "redis",
                "messaging.destination.name": channel,
                "messaging.operation": "process"
            })
            try:
                await self._handle_event_logic(channel, event_data_str)
            except Exception as e: # Catchall for safety
                logger.error(f"Unhandled error in _handle_event_logic from channel {channel}: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Event handling logic failed"))


    async def _handle_event_logic(self, channel: str, event_data_str: str):
        logger.debug(f"ReportingAgent received raw message on channel '{channel}': {message_summary(event_data_str)}")
        try:
            event_data = json.loads(event_data_str)
            event_type = event_data.get("event_type", "UnknownEvent")

            if self.tracer_instance:
                trace.get_current_span().set_attribute("event.type", event_type)
                trace.get_current_span().set_attribute("event.project_id", event_data.get("project_id", "N/A"))


            # You can have specific handlers or formatting based on event_type
            # For V0.1, we'll use a generic formatter and pusher
            report = self.format_report(event_type, event_data, channel)
            await self.push_to_sink(report) # Make push_to_sink async if it involves I/O

        except json.JSONDecodeError:
            logger.error(f"Could not decode JSON from event on channel '{channel}': {message_summary(event_data_str)}")
            if self.tracer_instance: trace.get_current_span().set_status(trace.Status(trace.StatusCode.ERROR, "JSONDecodeError"))
        except Exception as e:
            logger.error(f"Error processing event from channel '{channel}': {e}", exc_info=True)
            if self.tracer_instance: 
                trace.get_current_span().record_exception(e)
                trace.get_current_span().set_status(trace.Status(trace.StatusCode.ERROR, "Generic event processing error"))


    async def main_event_loop(self):
        if not self.event_bus.redis_client:
            logger.critical("ReportingAgent: Cannot start main event loop, EventBus not connected. Exiting.")
            await asyncio.sleep(60) # Avoid rapid restarts
            return

        pubsub = self.event_bus.redis_client.pubsub()
        subscribed_ok = False
        try:
            # psubscribe to all configured patterns
            for pattern in EVENT_CHANNELS_PATTERNS:
                await asyncio.to_thread(pubsub.psubscribe, pattern)
                logger.info(f"ReportingAgent subscribed to pattern '{pattern}'")
            subscribed_ok = True
        except redis.exceptions.RedisError as e:
            logger.critical(f"ReportingAgent: Failed to psubscribe to event patterns: {e}. Worker cannot start.")
            return

        if not subscribed_ok: # Should not happen if loop continues
            logger.critical("ReportingAgent: No subscriptions successful. Worker exiting.")
            return

        logger.info(f"ReportingAgent worker started, listening for events on patterns: {EVENT_CHANNELS_PATTERNS}")
        try:
            while True:
                message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "pmessage": # pmessage for pattern subscriptions
                    # Create a new trace for each message received
                    # This is a simplified trace creation; context propagation from event would be better
                    span_name = "reporting_agent.process_event_from_bus"
                    if not self.tracer_instance:
                        await self.handle_event(message["channel"], message["data"])
                    else:
                         with self.tracer_instance.start_as_current_span(span_name) as span:
                            span.set_attribute("messaging.source.channel", message.get("channel"))
                            await self.handle_event(message["channel"], message["data"])

                await asyncio.sleep(0.01) # Yield control briefly
        except redis.exceptions.ConnectionError as redis_err:
            logger.error(f"Redis connection error in ReportingAgent main loop: {redis_err}.", exc_info=True)
            raise # Re-raise to be caught by main execution block for shutdown
        except KeyboardInterrupt:
            logger.info("ReportingAgent event loop interrupted.")
        except Exception as e:
            logger.error(f"Critical error in ReportingAgent event loop: {e}", exc_info=True)
        finally:
            logger.info("ReportingAgent shutting down pubsub...")
            if pubsub:
                try: 
                    for pattern in EVENT_CHANNELS_PATTERNS:
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
