# =======================================
# 📁 interfaces/sdk/debugiq_sdk.py
# =======================================
import os
import httpx
import logging
from typing import Optional, Dict, Any, List

# Assuming DebugIQ has its own set of exceptions, or we can use generic ones
# from ....sdk.exceptions import APIError, AuthenticationError, NotFoundError # If using SDK from root

logger = logging.getLogger(__name__)

DEBUGIQ_API_BASE_URL_ENV = "DEBUGIQ_API_BASE_URL"
DEBUGIQ_API_KEY_ENV = "DEBUGIQ_API_KEY"

class DebugIQClient:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: int = 30):
        self.base_url = base_url or os.getenv(DEBUGIQ_API_BASE_URL_ENV)
        self.api_key = api_key or os.getenv(DEBUGIQ_API_KEY_ENV)

        if not self.base_url:
            msg = f"DebugIQClient: {DEBUGIQ_API_BASE_URL_ENV} not set."
            logger.error(msg)
            raise ValueError(msg)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}" # Or other auth scheme

        self.http_client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout)
        logger.info(f"DebugIQClient initialized for base URL: {self.base_url}")

    async def close(self):
        await self.http_client.aclose()
        logger.info("DebugIQClient HTTP client closed.")

    async def _request(self, method: str, endpoint: str, json_data: Optional[Dict] = None, params: Optional[Dict] = None) -> Dict[str, Any]:
        try:
            response = await self.http_client.request(method, endpoint, json=json_data, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            # Log and re-raise a more specific or generic SDK error
            logger.error(f"DebugIQ API Error: {e.response.status_code} calling {e.request.url}. Response: {e.response.text[:200]}")
            # raise APIError(...)
            raise
        except httpx.RequestError as e:
            logger.error(f"DebugIQ Request Error: {e.request.method} {e.request.url} - {e}")
            raise

    async def get_analysis_results(self, project_id: str, analysis_type: str) -> Dict[str, Any]:
        """Example: Get analysis results from DebugIQ."""
        endpoint = f"/api/v1/projects/{project_id}/analysis/{analysis_type}"
        logger.info(f"DebugIQClient: Fetching analysis '{analysis_type}' for project '{project_id}'")
        return await self._request("GET", endpoint)

    async def submit_code_for_debugging(self, project_id: str, code_snippet: str, language: str) -> Dict[str, Any]:
        """Example: Submit code to DebugIQ for a debugging session."""
        endpoint = f"/api/v1/projects/{project_id}/debug-sessions"
        payload = {"code": code_snippet, "language": language}
        logger.info(f"DebugIQClient: Submitting code for debugging for project '{project_id}'")
        return await self._request("POST", endpoint, json_data=payload)

    # Add more methods here to interact with other DebugIQ API endpoints
