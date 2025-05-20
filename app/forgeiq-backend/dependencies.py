# =================================================
# 📁 app/forgeiq-backend/dependencies.py
# =================================================
import os
import logging
from typing import Optional, AsyncGenerator

import httpx
from fastapi import Security, HTTPException, status as fastapi_status
from fastapi.security.api_key import APIKeyHeader

# Assuming core modules are importable via PYTHONPATH
from core.event_bus.redis_bus import EventBus
from core.message_router import MessageRouter
from core.shared_memory import SharedMemoryStore

logger = logging.getLogger(__name__)

# --- Configuration for API Key ---
API_KEY_NAME = "X-API-Key" # Standard header name for API keys
FORGEIQ_BACKEND_API_KEY = os.getenv("FORGEIQ_BACKEND_API_KEY")
from fastapi import Security, HTTPException

def get_api_key(api_key: str = Security(...)):  # Replace `...` with actual security implementation
    """Validates API key for secured endpoints."""
    if api_key != "your-secure-api-key":  # ✅ Replace with your real validation logic
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key
api_key_header_auth = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

async def get_api_key(api_key_header: str = Security(api_key_header_auth)):
    """Dependency to validate the API key."""
    if not FORGEIQ_BACKEND_API_KEY:
        logger.warning("API Key not configured in environment, but endpoint requires it. Denying access.")
        # If no API key is configured on the server, no key can be valid.
        # You might choose to allow access if no server key is set (e.g., for local dev without auth),
        # but for production, this should be an error.
        raise HTTPException(
            status_code=fastapi_status.HTTP_401_UNAUTHORIZED,
            detail="API Key not configured on server."
        )
    if api_key_header == FORGEIQ_BACKEND_API_KEY:
        return api_key_header
    else:
        logger.warning(f"Invalid API Key received: {api_key_header[:5]}...") # Log a redacted portion
        raise HTTPException(
            status_code=fastapi_status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key or missing credentials."
        )

# --- Core Service Instances (managed as singletons for the app lifecycle) ---
# These will be initialized during FastAPI app lifespan events.
# We define provider functions that will yield these instances.

_event_bus_instance: Optional[EventBus] = None
_message_router_instance: Optional[MessageRouter] = None
_shared_memory_instance: Optional[SharedMemoryStore] = None
_private_intel_http_client: Optional[httpx.AsyncClient] = None


async def init_core_services():
    """Initializes core services. Called during FastAPI lifespan startup."""
    global _event_bus_instance, _message_router_instance, _shared_memory_instance, _private_intel_http_client

    logger.info("Initializing core services for ForgeIQ-Backend...")

    _event_bus_instance = EventBus() # Assumes REDIS_URL is globally available via os.getenv
    if not _event_bus_instance.redis_client:
        logger.error("Failed to initialize EventBus: Redis connection failed.")
        # Decide if app should start without EventBus. For now, it will, but log an error.

    _message_router_instance = MessageRouter(_event_bus_instance) # MessageRouter needs an EventBus

    _shared_memory_instance = SharedMemoryStore() # Assumes REDIS_URL
    if not _shared_memory_instance.redis_client:
        logger.error("Failed to initialize SharedMemoryStore: Redis connection failed.")

    # Initialize httpx client for Private Intelligence API
    private_intel_api_base_url = os.getenv("PRIVATE_INTELLIGENCE_API_BASE_URL")
    private_intel_api_key = os.getenv("PRIVATE_INTELLIGENCE_API_KEY")
    if private_intel_api_base_url:
        headers = {"Content-Type": "application/json"}
        if private_intel_api_key:
            headers["Authorization"] = f"Bearer {private_intel_api_key}"

        _private_intel_http_client = httpx.AsyncClient(
            base_url=private_intel_api_base_url, 
            headers=headers, 
            timeout=120.0
        )
        logger.info(f"HTTP client for Private Intelligence API initialized, targeting: {private_intel_api_base_url}")
    else:
        logger.warning("PRIVATE_INTELLIGENCE_API_BASE_URL not set. Private Intelligence API calls will fail.")

    logger.info("Core services initialization attempt complete.")


async def close_core_services():
    """Closes core services. Called during FastAPI lifespan shutdown."""
    global _private_intel_http_client
    if _private_intel_http_client:
        await _private_intel_http_client.aclose()
        logger.info("Private Intelligence API HTTP client closed.")
    # EventBus and SharedMemoryStore using redis-py client don't always need explicit async close,
    # but if they held other resources, this would be the place.


# --- Dependency Provider Functions ---
# These are used by FastAPI's Depends()

def get_event_bus() -> EventBus:
    if _event_bus_instance is None or _event_bus_instance.redis_client is None:
        # This state indicates a critical failure during startup if Redis is essential.
        logger.error("EventBus requested but not available or not connected to Redis.")
        raise HTTPException(status_code=503, detail="Event Bus service is currently unavailable.")
    return _event_bus_instance

def get_message_router() -> MessageRouter:
    if _message_router_instance is None or not getattr(_message_router_instance.event_bus, 'redis_client', None) :
        logger.error("MessageRouter requested but not available or its EventBus is not connected.")
        raise HTTPException(status_code=503, detail="Message Router service is currently unavailable.")
    return _message_router_instance

def get_shared_memory_store() -> SharedMemoryStore:
    if _shared_memory_instance is None or _shared_memory_instance.redis_client is None:
        logger.error("SharedMemoryStore requested but not available or not connected to Redis.")
        raise HTTPException(status_code=503, detail="Shared Memory service is currently unavailable.")
    return _shared_memory_instance

def get_private_intel_client() -> httpx.AsyncClient:
    if _private_intel_http_client is None:
        logger.error("Private Intelligence API client requested but not available (check PRIVATE_INTELLIGENCE_API_BASE_URL).")
        raise HTTPException(status_code=503, detail="Private Intelligence API client is not configured or available.")
    return _private_intel_http_client
