# ============================================
# 📁 core/shared_memory/redis_store.py
# ============================================
import os
import json
import logging
from typing import Any, Optional, Dict, List
import redis # For Redis client and exceptions

# Attempt to import OpenTelemetry for tracing, but make it optional
# so the module can function without OTel if not configured.
tracer = None
trace_api = None
try:
    from opentelemetry import trace as otel_trace_api # Renamed to avoid conflict
    tracer = otel_trace_api.get_tracer(__name__)
    trace_api = otel_trace_api # To access trace.Status, trace.StatusCode
except ImportError:
    logging.getLogger(__name__).info(
        "OpenTelemetry API not found. SharedMemoryStore will operate without distributed tracing."
    )

logger = logging.getLogger(__name__)

REDIS_URL_SHARED_MEM = os.getenv("REDIS_URL")
KEY_PREFIX = os.getenv("SHARED_MEMORY_REDIS_PREFIX", "agentic_system:shared_mem:")

class SharedMemoryStore:
    def __init__(self, redis_url: Optional[str] = None, key_prefix: Optional[str] = None):
        self.redis_url = redis_url or REDIS_URL_SHARED_MEM
        self.key_prefix = key_prefix or KEY_PREFIX
        self.redis_client: Optional[redis.Redis] = None

        if not self.redis_url:
            logger.error("REDIS_URL not configured. SharedMemoryStore will not connect.")
            return
        try:
            # decode_responses=False to handle bytes for JSON and other types consistently
            self.redis_client = redis.from_url(self.redis_url, decode_responses=False)
            self.redis_client.ping()
            logger.info(f"SharedMemoryStore connected to Redis at {self.redis_url} with prefix '{self.key_prefix}'")
        except redis.exceptions.ConnectionError as e:
            logger.error(f"SharedMemoryStore failed to connect to Redis: {e}")
            self.redis_client = None
        except Exception as e: # Catch any other potential errors during client creation
            logger.error(f"An unexpected error occurred during SharedMemoryStore Redis connection: {e}", exc_info=True)
            self.redis_client = None

    def _get_full_key(self, key: str) -> str:
        return f"{self.key_prefix}{key}"

    def _start_trace_span(self, operation_name: str, key: str, **extra_attrs):
        """Helper to start a trace span if tracer is available."""
        if tracer and trace_api: # Check both tracer and trace_api (for Status)
            span = tracer.start_span(f"shared_memory.{operation_name}")
            span.set_attribute("db.system", "redis")
            span.set_attribute("db.redis.key_prefix", self.key_prefix)
            span.set_attribute("db.redis.key_suffix", key)
            for k, v_attr in extra_attrs.items(): # Renamed to v_attr to avoid conflict
                span.set_attribute(k, v_attr)
            return span

        # Return a no-op context manager if no tracer
        class NoOpSpan:
            def __enter__(self): return self
            def __exit__(self, exc_type, exc_val, exc_tb): pass
            def set_attribute(self, key_attr, value_attr): pass # Renamed for clarity
            def record_exception(self, exception): pass
            def set_status(self, status): pass
            def end(self): pass
        return NoOpSpan()

    async def set_value(self, key: str, value: Any, expiry_seconds: Optional[int] = None) -> bool:
        if not self.redis_client:
            logger.warning(f"Cannot set value for '{key}': Redis client not available.")
            return False

        full_key = self._get_full_key(key)
        span = self._start_trace_span("set_value", key, expiry_seconds=str(expiry_seconds or "none"))

        try:
            with span: # type: ignore
                serialized_value: bytes
                value_type_for_span = "unknown"
                if isinstance(value, (dict, list)):
                    serialized_value = json.dumps(value).encode('utf-8')
                    value_type_for_span = "json"
                elif isinstance(value, (int, float)):
                    serialized_value = str(value).encode('utf-8')
                    value_type_for_span = "number"
                elif isinstance(value, str):
                    serialized_value = value.encode('utf-8')
                    value_type_for_span = "string"
                elif isinstance(value, bytes):
                    serialized_value = value
                    value_type_for_span = "bytes"
                else:
                    logger.error(f"Unsupported value type for key '{full_key}': {type(value)}. Must be serializable.")
                    if tracer and trace_api: span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Unsupported value type"))
                    return False

                if tracer: span.set_attribute("db.redis.value_type", value_type_for_span)

                if expiry_seconds:
                    self.redis_client.set(full_key, serialized_value, ex=expiry_seconds)
                else:
                    self.redis_client.set(full_key, serialized_value)
                logger.debug(f"Set value for key '{full_key}' (type: {value_type_for_span}) with expiry {expiry_seconds or 'None'}")
                if tracer and trace_api: span.set_status(trace_api.Status(trace_api.StatusCode.OK))
                return True
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis error setting value for key '{full_key}': {e}")
            if tracer and trace_api: span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Redis error"))
        except Exception as e:
            logger.error(f"Unexpected error setting value for key '{full_key}': {e}", exc_info=True)
            if tracer and trace_api: span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Unexpected error"))
        return False

    async def get_value(self, key: str, expected_type: type = str) -> Optional[Any]:
        if not self.redis_client:
            logger.warning(f"Cannot get value for '{key}': Redis client not available.")
            return None

        full_key = self._get_full_key(key)
        span = self._start_trace_span("get_value", key, expected_type=str(expected_type))

        try:
            with span: # type: ignore
                raw_value: Optional[bytes] = self.redis_client.get(full_key)
                if raw_value is None:
                    logger.debug(f"No value found for key '{full_key}'")
                    if tracer: span.set_attribute("db.redis.value_found", False)
                    return None

                if tracer: span.set_attribute("db.redis.value_found", True)

                deserialized_value: Optional[Any] = None
                if expected_type == dict or expected_type == list:
                    try:
                        deserialized_value = json.loads(raw_value.decode('utf-8'))
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to decode JSON for key '{full_key}': {e}. Raw (first 100 bytes): {raw_value[:100]}")
                        if tracer and trace_api: span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "JSON decode error"))
                        return None
                elif expected_type == int:
                    try:
                        deserialized_value = int(raw_value.decode('utf-8'))
                    except ValueError as e:
                        logger.error(f"Failed to cast to int for key '{full_key}': {e}. Raw: {raw_value[:100]}")
                        if tracer and trace_api: span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Int cast error"))
                        return None
                elif expected_type == float:
                    try:
                        deserialized_value = float(raw_value.decode('utf-8'))
                    except ValueError as e:
                        logger.error(f"Failed to cast to float for key '{full_key}': {e}. Raw: {raw_value[:100]}")
                        if tracer and trace_api: span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Float cast error"))
                        return None
                elif expected_type == bytes:
                    deserialized_value = raw_value
                elif expected_type == str: # Default
                    deserialized_value = raw_value.decode('utf-8')
                else:
                    logger.warning(f"Unexpected expected_type '{str(expected_type)}' for key '{full_key}'. Returning as string.")
                    deserialized_value = raw_value.decode('utf-8')

                if tracer and trace_api: span.set_status(trace_api.Status(trace_api.StatusCode.OK))
                return deserialized_value
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis error getting value for key '{full_key}': {e}")
            if tracer and trace_api: span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Redis error"))
        except Exception as e:
            logger.error(f"Unexpected error getting value for key '{full_key}': {e}", exc_info=True)
            if tracer and trace_api: span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Unexpected error"))
        return None

    async def delete_value(self, key: str) -> bool:
        if not self.redis_client:
            logger.warning(f"Cannot delete value for '{key}': Redis client not available.")
            return False

        full_key = self._get_full_key(key)
        span = self._start_trace_span("delete_value", key)

        try:
            with span: # type: ignore
                result = self.redis_client.delete(full_key)
                deleted_count = int(result)
                logger.debug(f"Deleted {deleted_count} key(s) for full_key '{full_key}'")
                if tracer: 
                    span.set_attribute("db.redis.deleted_count", deleted_count)
                    if trace_api: span.set_status(trace_api.Status(trace_api.StatusCode.OK))
                return deleted_count > 0
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis error deleting value for key '{full_key}': {e}")
            if tracer and trace_api: span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Redis error"))
        except Exception as e:
            logger.error(f"Unexpected error deleting value for key '{full_key}': {e}", exc_info=True)
            if tracer and trace_api: span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Unexpected error"))
        return False

    async def exists(self, key: str) -> bool:
        if not self.redis_client:
            logger.warning(f"Cannot check existence for '{key}': Redis client not available.")
            return False

        full_key = self._get_full_key(key)
        span = self._start_trace_span("exists", key)

        try:
            with span: # type: ignore
                result = self.redis_client.exists(full_key)
                key_exists = int(result) > 0
                if tracer: 
                    span.set_attribute("db.redis.key_exists", key_exists)
                    if trace_api: span.set_status(trace_api.Status(trace_api.StatusCode.OK))
                return key_exists
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis error checking existence for key '{full_key}': {e}")
            if tracer and trace_api: span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Redis error"))
        except Exception as e:
            logger.error(f"Unexpected error checking existence for key '{full_key}': {e}", exc_info=True)
            if tracer and trace_api: span.record_exception(e); span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, "Unexpected error"))
        return False
    ```
