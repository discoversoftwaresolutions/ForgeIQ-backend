# ==============================================================
# 📁 core/event_bus/redis_bus.py
# (Within your ForgeIQ project structure)
# ==============================================================
import redis  # For Redis client and exceptions
import json
import os
import logging
from typing import Dict, Any, Optional, Callable, Union # For type hints

# OpenTelemetry imports - these define 'trace' and 'trace_api' for use in this module
# It's assumed that the main application using this EventBus will have already
# called setup_tracing() from core.observability to configure the global tracer provider.
try:
    from opentelemetry import trace as otel_trace_api_for_bus # Use a distinct alias
    tracer = otel_trace_api_for_bus.get_tracer("core.event_bus", "0.1.1") # Get a named tracer
    trace_api = otel_trace_api_for_bus # For Status, StatusCode if needed by spans
except ImportError:
    logging.getLogger(__name__).info(
        "EventBus: OpenTelemetry API not found or not configured. Tracing for EventBus operations will be disabled."
    )
    tracer = None # type: ignore
    trace_api = None # type: ignore

logger = logging.getLogger(__name__)

REDIS_URL_FOR_EVENT_BUS = os.getenv("REDIS_URL", "redis://localhost:6379")

def message_summary(event_data: Any, max_len: int = 100) -> str:
    try:
        if isinstance(event_data, dict):
            summary = json.dumps(event_data)
        elif isinstance(event_data, bytes):
            summary = event_data.decode('utf-8', errors='replace')
        else:
            summary = str(event_data)
    except TypeError:
        summary = f"Non-serializable event data of type {type(event_data)}"
    return summary[:max_len-3] + "..." if len(summary) > max_len else summary

class EventBus:
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url_to_use = redis_url or REDIS_URL_FOR_EVENT_BUS
        self.redis_client: Optional[redis.Redis] = None

        if not self.redis_url_to_use:
            logger.error("EventBus: REDIS_URL not configured. EventBus will not connect.")
            return
        try:
            self.redis_client = redis.from_url(self.redis_url_to_use, decode_responses=True)
            self.redis_client.ping()
            logger.info(f"EventBus connected to Redis at {self.redis_url_to_use}")
        except redis.exceptions.ConnectionError as e:
            logger.error(f"EventBus failed to connect to Redis at {self.redis_url_to_use}: {e}")
            self.redis_client = None
        except Exception as e:
            logger.error(f"EventBus: Unexpected error during Redis connection to {self.redis_url_to_use}: {e}", exc_info=True)
            self.redis_client = None

    def _start_span(self, operation_name: str, **attrs) -> Any:
        if tracer and hasattr(tracer, 'start_span'): # Check if tracer is valid
            span = tracer.start_span(f"event_bus.{operation_name}")
            for k, v_attr in attrs.items():
                span.set_attribute(k, v_attr)
            return span

        # Fallback NoOpSpan if tracer is not available or not the expected type
        class NoOpSpan:
            def __enter__(self): return self
            def __exit__(self, exc_type, exc_val, exc_tb): pass
            def set_attribute(self, key, value): pass
            def record_exception(self, exception, attributes=None): pass
            def set_status(self, status): pass
            def end(self): pass
        return NoOpSpan()

    def publish(self, channel: str, event_data: Dict[str, Any]):
        span_attrs = {"messaging.system": "redis", "messaging.destination_kind": "topic", "messaging.destination.name": channel, "event.type": str(event_data.get("event_type"))}
        span = self._start_span("publish", **span_attrs)
        try:
            with span: #type: ignore
                if not self.redis_client:
                    logger.error(f"Cannot publish to '{channel}': EventBus Redis client not connected.")
                    if tracer and trace_api and hasattr(span, 'set_status'): span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Redis client not connected"))
                    return
                try:
                    message_string = json.dumps(event_data)
                    self.redis_client.publish(channel, message_string)
                    logger.info(f"EventBus: Published to channel '{channel}': {message_summary(event_data)}")
                    if tracer and trace_api and hasattr(span, 'set_status'): span.set_status(trace_api.Status(trace_api.StatusCode.OK))
                except TypeError as te: 
                    logger.error(f"TypeError publishing to channel '{channel}': Data not JSON serializable. Error: {te}. Data: {str(event_data)[:200]}")
                    if tracer and trace_api and hasattr(span, 'record_exception'): span.record_exception(te); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "JSON serialization error"))
                except redis.exceptions.RedisError as e:
                    logger.error(f"RedisError publishing to channel '{channel}': {e}", exc_info=True)
                    if tracer and trace_api and hasattr(span, 'record_exception'): span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Redis publish error"))
        except Exception as e_outer: 
             logger.error(f"Unexpected error in EventBus.publish for channel '{channel}': {e_outer}", exc_info=True)
             # Check if span is a real span before trying to record exception on it
             if tracer and trace_api and hasattr(span, 'record_exception') and hasattr(span, 'is_recording') and span.is_recording(): #type: ignore
                 span.record_exception(e_outer) #type: ignore
                 span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Unexpected publish error")) #type: ignore

    def subscribe_to_channel(self, channel_or_pattern: str) -> Optional[redis.client.PubSub]:
        span_attrs = {"messaging.system": "redis", "messaging.destination_kind": "topic", "messaging.destination.name": channel_or_pattern}
        span = self._start_span("subscribe", **span_attrs)
        try:
            with span: #type: ignore
                if not self.redis_client:
                    logger.error(f"Cannot subscribe to '{channel_or_pattern}': EventBus Redis client not connected.")
                    if tracer and trace_api and hasattr(span, 'set_status'): span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Redis client not connected"))
                    return None

                pubsub_client = self.redis_client.pubsub()
                try:
                    if any(c in channel_or_pattern for c in ['*', '?', '[']):
                        pubsub_client.psubscribe(channel_or_pattern)
                        logger.info(f"EventBus: Successfully psubscribed to pattern '{channel_or_pattern}'")
                    else:
                        pubsub_client.subscribe(channel_or_pattern)
                        logger.info(f"EventBus: Successfully subscribed to channel '{channel_or_pattern}'")
                    if tracer and trace_api and hasattr(span, 'set_status'): span.set_status(trace_api.Status(trace_api.StatusCode.OK))
                    return pubsub_client
                except redis.exceptions.RedisError as e:
                    logger.error(f"RedisError subscribing to '{channel_or_pattern}': {e}", exc_info=True)
                    if tracer and trace_api and hasattr(span, 'record_exception'): span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Redis subscribe error"))
                    return None
        except Exception as e_outer:
            logger.error(f"Unexpected error in EventBus.subscribe_to_channel for '{channel_or_pattern}': {e_outer}", exc_info=True)
            if tracer and trace_api and hasattr(span, 'record_exception') and hasattr(span, 'is_recording') and span.is_recording(): #type: ignore
                span.record_exception(e_outer) #type: ignore
                span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Unexpected subscribe error")) #type: ignore
        return None
