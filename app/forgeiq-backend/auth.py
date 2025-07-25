from fastapi.security import APIKeyHeader
from fastapi import Security, HTTPException

# ✅ Define the security dependency correctly
api_key_header = APIKeyHeader(name="Authorization", auto_error=True)

def get_api_key(api_key: str = Security(api_key_header)):  # ✅ Correct usage
    """Validates API key for secured endpoints."""
    VALID_API_KEYS = ["your-secure-api-key", "another-valid-key"]

    if api_key not in VALID_API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    
    return api_key
import httpx

async def get_private_intel_client() -> httpx.AsyncClient:
    """Initializes an HTTP client for the Private Intelligence Stack."""
    return httpx.AsyncClient(base_url="https://private-intelligence-service.com")  # ✅ Replace with actual endpoint
