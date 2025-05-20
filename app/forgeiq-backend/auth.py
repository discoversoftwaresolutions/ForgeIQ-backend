from fastapi import Security, HTTPException
import httpx

async def get_private_intel_client() -> httpx.AsyncClient:
    """Initializes an HTTP client for the Private Intelligence Stack."""
    return httpx.AsyncClient(base_url="https://private-intelligence-service.com")  # ✅ Replace with actual endpoint
def get_api_key(api_key: str = Security(...)):  # Replace `...` with actual validation logic
    if api_key != "your-secure-api-key":  # ✅ Implement your API key validation mechanism
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key
