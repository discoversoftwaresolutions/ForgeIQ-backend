from fastapi import Security, HTTPException
import httpx

# ✅ Explicitly defining available exports
__all__ = ["get_private_intel_client", "get_api_key"]

async def get_private_intel_client() -> httpx.AsyncClient:
    """Initializes an HTTP client for the Private Intelligence Stack."""
    return httpx.AsyncClient(base_url="https://private-intelligence-service.com")  # ✅ Replace with actual endpoint

def get_api_key(api_key: str = Security(...)):  # ✅ Define your security dependency here
    """Validates API key for secured endpoints."""
    VALID_API_KEYS = ["your-secure-api-key", "another-valid-key"]  # ✅ Replace with actual API key management
    if api_key not in VALID_API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key
