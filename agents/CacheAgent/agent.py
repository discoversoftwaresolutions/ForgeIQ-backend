# agents/CacheAgent/agent.py
import os
import json
import hashlib
import datetime
import logging
from typing import Dict, Any, Optional, List

import redis
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel, Field # For API request/response models

# --- Observability Setup ---
SERVICE_NAME = "CacheAgent"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format=f'%(asctime)s - {SERVICE_NAME} - %(name)s - %(levelname)s - %(message)s'
)
tracer = None
try:
    from core.observability.tracing import setup_tracing
    tracer = setup_tracing(SERVICE_NAME)
except ImportError:
    logging.getLogger(SERVICE_NAME).warning(
        "CacheAgent: Tracing setup failed. Ensure core.observability.tracing is available."
    )
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

# Import a subset of types for clarity, or use `from interfaces.types import cache as CacheTypes`
from interfaces.types.cache import (
    CacheKeyParams, CachedItemMetadata, CacheGetResponse, 
    CacheStoreRequest, CacheStoreResponse
)

REDIS_URL = os.getenv("REDIS_URL")
DEFAULT_CACHE_EXPIRY_SECONDS = int(os.getenv("CACHE_EXPIRY_SECONDS", 30 * 24 * 60 * 60)) # Default 30 days

# --- Pydantic Models for API (FastAPI uses these for validation & serialization) ---
# These mirror the TypedDicts but are Pydantic models.
# In a larger system, you might generate Pydantic models from TypedDicts or share base models.
class ApiCacheKeyParams(BaseModel):
    project_id: str
    task_id: str
    task_type: str
    input_file_hashes: Dict[str, str]
    task_parameters: Dict[str, Any]
    tool_version: Optional[str] = None

class ApiCachedItemMetadata(BaseModel):
    cache_key: str
    created_at: str
    task_id: str
    project_id: str
    result_type: str

class ApiCacheGetResponse(BaseModel):
    is_hit: bool
    cache_key: Optional[str] = None
    metadata: Optional[ApiCachedItemMetadata] = None
    cached_result: Optional[Any] = None

class ApiCacheStoreRequest(BaseModel):
    cache_key: str 
    project_id: str
    task_id: str
    result_to_cache: Any
    result_type: str

class ApiCacheStoreResponse(BaseModel):
    success: bool
    cache_key: str
    message: Optional[str] = None
# --- End Pydantic Models ---


class CacheManager:
    def __init__(self):
        logger.info("Initializing CacheManager...")
        self.redis_client = None
        if not REDIS_URL:
            logger.error("REDIS_URL not set. CacheManager will operate in no-cache mode.")
        else:
            try:
                self.redis_client = redis.from_url(REDIS_URL, decode_responses=False) # Store raw bytes for result
                self.redis_client.ping()
                logger.info(f"CacheManager connected to Redis at {REDIS_URL}")
            except redis.exceptions.ConnectionError as e:
                logger.error(f"CacheManager failed to connect to Redis: {e}. Operating in no-cache mode.")
                self.redis_client = None

    @property
    def tracer_instance(self): # For OpenTelemetry
        return tracer

    def _generate_fingerprint(self, params: CacheKeyParams) -> str:
        """Generates a deterministic SHA256 hash for the given parameters."""
        # Ensure consistent ordering for hashing
        # Sort file hashes by path
        sorted_file_hashes = sorted(params["input_file_hashes"].items())
        # Sort task parameters by key
        sorted_task_params = sorted(params["task_parameters"].items())

        data_to_hash = {
            "project_id": params["project_id"],
            "task_id": params["task_id"],
            "task_type": params["task_type"],
            "input_file_hashes": sorted_file_hashes,
            "task_parameters": sorted_task_params,
            "tool_version": params.get("tool_version")
        }
        # Use json.dumps with sort_keys for a canonical representation
        canonical_string = json.dumps(data_to_hash, sort_keys=True)
        return hashlib.sha256(canonical_string.encode('utf-8')).hexdigest()

    async def get_cache_key(self, params: ApiCacheKeyParams) -> str:
        # Convert Pydantic model to TypedDict if necessary for _generate_fingerprint
        # For this example, assuming _generate_fingerprint can handle dict-like Pydantic model
        # or convert params.model_dump() to CacheKeyParams TypedDict.
        # For simplicity, we'll pass the Pydantic model which behaves like a dict.
        typed_dict_params = CacheKeyParams(**params.model_dump()) # Convert Pydantic to TypedDict
        return self._generate_fingerprint(typed_dict_params)

    async def check_cache(self, cache_key: str) -> ApiCacheGetResponse:
        span_name = "cache_agent.check_cache"
        if not self.tracer_instance: # Fallback
            return self._check_cache_logic(cache_key)

        with self.tracer_instance.start_as_current_span(span_name) as span:
            span.set_attribute("cache.key", cache_key)
            response = self._check_cache_logic(cache_key)
            span.set_attribute("cache.hit", response.is_hit)
            return response

    def _check_cache_logic(self, cache_key: str) -> ApiCacheGetResponse:
        if not self.redis_client:
            logger.debug(f"Cache check for key {cache_key}: No Redis client, returning miss.")
            return ApiCacheGetResponse(is_hit=False, cache_key=cache_key)

        try:
            # Cache stores two keys: one for metadata, one for the actual result blob
            metadata_key = f"cache:metadata:{cache_key}"
            result_key = f"cache:result:{cache_key}"

            raw_metadata = self.redis_client.get(metadata_key)
            if not raw_metadata:
                logger.info(f"Cache miss for key: {cache_key} (metadata not found)")
                return ApiCacheGetResponse(is_hit=False, cache_key=cache_key)

            metadata_dict = json.loads(raw_metadata.decode('utf-8')) # Metadata stored as JSON string
            # Convert to Pydantic model for response consistency
            metadata = ApiCachedItemMetadata(**CachedItemMetadata(**metadata_dict)) 


            raw_result = self.redis_client.get(result_key) # This will be bytes
            if not raw_result:
                logger.warning(f"Cache inconsistency for key {cache_key}: Metadata found but result blob missing. Treating as miss.")
                # Optionally delete the stale metadata key here
                # self.redis_client.delete(metadata_key)
                return ApiCacheGetResponse(is_hit=False, cache_key=cache_key)

            cached_result: Any
            if metadata.result_type == "json_dict":
                cached_result = json.loads(raw_result.decode('utf-8'))
            elif metadata.result_type == "text_log":
                cached_result = raw_result.decode('utf-8')
            else: # "file_path_pointer" or unknown, return as raw bytes or a placeholder
                logger.warning(f"Unsupported or pointer result_type '{metadata.result_type}' for key {cache_key}. Returning raw or placeholder.")
                cached_result = {"type": metadata.result_type, "note": "Raw data would be here or pointer to S3/Artifacts"}
                # For now, to make it JSON serializable, let's just return the note.
                # In a real case with S3, you'd return S3 URL from metadata.

            logger.info(f"Cache hit for key: {cache_key}")
            return ApiCacheGetResponse(
                is_hit=True, 
                cache_key=cache_key, 
                metadata=metadata,
                cached_result=cached_result
            )

        except redis.exceptions.RedisError as e:
            logger.error(f"Redis error during cache check for key {cache_key}: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error for cached metadata/result for key {cache_key}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during cache check for key {cache_key}: {e}", exc_info=True)

        return ApiCacheGetResponse(is_hit=False, cache_key=cache_key) # Default to miss on error

    async def store_cache(self, request_data: ApiCacheStoreRequest) -> ApiCacheStoreResponse:
        span_name = "cache_agent.store_cache"
        if not self.tracer_instance: # Fallback
            return self._store_cache_logic(request_data)

        with self.tracer_instance.start_as_current_span(span_name) as span:
            span.set_attributes({
                "cache.key": request_data.cache_key,
                "cache.project_id": request_data.project_id,
                "cache.task_id": request_data.task_id,
                "cache.result_type": request_data.result_type
            })
            response = self._store_cache_logic(request_data)
            span.set_attribute("cache.store_success", response.success)
            if not response.success and response.message:
                 span.set_attribute("cache.store_error", response.message)
            return response

    def _store_cache_logic(self, request_data: ApiCacheStoreRequest) -> ApiCacheStoreResponse:
        if not self.redis_client:
            logger.debug(f"Cache store for key {request_data.cache_key}: No Redis client, operation skipped.")
            return ApiCacheStoreResponse(success=False, cache_key=request_data.cache_key, message="Redis client not available")

        cache_key = request_data.cache_key
        metadata_key = f"cache:metadata:{cache_key}"
        result_key = f"cache:result:{cache_key}"

        try:
            # Prepare metadata
            metadata_to_store = CachedItemMetadata(
                cache_key=cache_key,
                created_at=datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
                task_id=request_data.task_id,
                project_id=request_data.project_id,
                result_type=request_data.result_type
            )
            serialized_metadata = json.dumps(metadata_to_store)

            # Prepare result (ensure it's bytes for Redis)
            result_bytes: bytes
            if request_data.result_type == "json_dict":
                result_bytes = json.dumps(request_data.result_to_cache).encode('utf-8')
            elif request_data.result_type == "text_log":
                result_bytes = str(request_data.result_to_cache).encode('utf-8')
            else: # Handle other types or raw bytes if result_to_cache is already bytes
                if isinstance(request_data.result_to_cache, bytes):
                    result_bytes = request_data.result_to_cache
                else:
                    logger.warning(f"Unsupported result_type '{request_data.result_type}' for storing result directly in Redis. Storing as string.")
                    result_bytes = str(request_data.result_to_cache).encode('utf-8')

            # Store in Redis using a pipeline for atomicity
            pipe = self.redis_client.pipeline()
            pipe.set(metadata_key, serialized_metadata, ex=DEFAULT_CACHE_EXPIRY_SECONDS)
            pipe.set(result_key, result_bytes, ex=DEFAULT_CACHE_EXPIRY_SECONDS)
            pipe.execute()

            logger.info(f"Successfully stored cache for key: {cache_key}")
            return ApiCacheStoreResponse(success=True, cache_key=cache_key, message="Cache stored successfully")

        except redis.exceptions.RedisError as e:
            logger.error(f"Redis error during cache store for key {cache_key}: {e}")
            return ApiCacheStoreResponse(success=False, cache_key=cache_key, message=f"Redis error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during cache store for key {cache_key}: {e}", exc_info=True)
            return ApiCacheStoreResponse(success=False, cache_key=cache_key, message=f"Unexpected error: {e}")

# --- FastAPI Application ---
# This will be the entry point for the CacheAgent service
# It exposes the CacheManager's functionality over HTTP.
cache_manager = CacheManager()
api_app = FastAPI(title=f"{SERVICE_NAME} API")

# Instrument FastAPI app if OTel is setup and if FastAPIInstrumentor is available
# try:
#     from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
#     if tracer: # Check if tracing was initialized
#         FastAPIInstrumentor.instrument_app(api_app, tracer_provider=tracer.provider if tracer else None)
#         logger.info("FastAPI app instrumented with OpenTelemetry for CacheAgent.")
# except ImportError:
#     logger.info("FastAPIInstrumentor not available. Skipping FastAPI auto-instrumentation for CacheAgent.")


@api_app.post("/get_cache_key", response_model=str, summary="Generate a cache key for given parameters")
async def get_cache_key_endpoint(params: ApiCacheKeyParams = Body(...)):
    try:
        return await cache_manager.get_cache_key(params)
    except Exception as e:
        logger.error(f"Error in /get_cache_key endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error generating cache key")


@api_app.post("/check", response_model=ApiCacheGetResponse, summary="Check if an item exists in cache")
async def check_cache_endpoint(cache_key: str = Body(..., embed=True)):
    # `embed=True` means the request body should be {"cache_key": "your_key_here"}
    try:
        return await cache_manager.check_cache(cache_key)
    except Exception as e:
        logger.error(f"Error in /check endpoint for key {cache_key}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error checking cache")

@api_app.post("/store", response_model=ApiCacheStoreResponse, summary="Store an item in the cache")
async def store_cache_endpoint(request_data: ApiCacheStoreRequest = Body(...)):
    try:
        return await cache_manager.store_cache(request_data)
    except Exception as e:
        logger.error(f"Error in /store endpoint for key {request_data.cache_key}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error storing cache")

@api_app.get("/health", summary="Health check")
async def health_check():
    # Check Redis connection if client was expected
    if REDIS_URL and not cache_manager.redis_client:
         raise HTTPException(status_code=503, detail="CacheAgent unhealthy: Redis not connected")
    return {"status": f"{SERVICE_NAME} is healthy"}

# To run this FastAPI app (e.g., in the Docker CMD):
# uvicorn agents.CacheAgent.app.agent:api_app --host 0.0.0.0 --port $PORT
# The if __name__ == "__main__" block is for direct execution if needed,
# but Uvicorn is preferred for running FastAPI.
# We'll make the Dockerfile CMD run uvicorn directly.

# No main event loop needed if this agent is purely API-driven by PlanAgent/TaskRunner
