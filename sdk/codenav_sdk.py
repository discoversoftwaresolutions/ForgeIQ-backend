# =======================================
# 📁 interfaces/sdk/codenav_sdk.py
# =======================================
import os
import httpx
import logging
from typing import Optional, Dict, Any, List

# Import response models - these should align with CodeNavAgent's API Pydantic models
# For now, let's assume some basic structures.
# from ....sdk.models import SDKCodeNavSearchResultItem # Example if reusing from main SDK
# Or define specific ones here if CodeNav's API output is distinct
class CodeNavSearchResult(TypedDict): # Placeholder
    file_path: str
    snippet: str
    score: float
    # ... other fields CodeNavAgent API returns ...

logger = logging.getLogger(__name__)

CODENAV_API_BASE_URL_ENV = "CODE_NAV_AGENT_API_URL" # e.g., http://codenav-agent-on-railway:8001

class CodeNavSDKClient:
    def __init__(self, base_url: Optional[str] = None, timeout: int = 60):
        self.base_url = base_url or os.getenv(CODENAV_API_BASE_URL_ENV)

        if not self.base_url:
            msg = f"CodeNavSDKClient: {CODENAV_API_BASE_URL_ENV} not set."
            logger.error(msg)
            raise ValueError(msg)

        self.http_client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        logger.info(f"CodeNavSDKClient initialized for base URL: {self.base_url}")

    async def close(self):
        await self.http_client.aclose()
        logger.info("CodeNavSDKClient HTTP client closed.")

    async def _request(self, method: str, endpoint: str, json_data: Optional[Dict] = None) -> Dict[str, Any]:
        try:
            response = await self.http_client.request(method, endpoint, json=json_data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"CodeNav API Error: {e.response.status_code} calling {e.request.url}. Resp: {e.response.text[:200]}")
            raise # Or map to specific SDK exceptions
        except httpx.RequestError as e:
            logger.error(f"CodeNav Request Error: {e.request.method} {e.request.url} - {e}")
            raise

    async def search_code(self, project_id: str, query_text: str, limit: int = 5, filters: Optional[Dict[str, Any]] = None) -> List[CodeNavSearchResult]:
        """Searches code using CodeNavAgent."""
        endpoint = "/search" # Matches the /search endpoint in CodeNavAgent's FastAPI app
        payload = {
            "project_id": project_id,
            "query_text": query_text,
            "limit": limit,
            "filters": filters or {}
        }
        logger.info(f"CodeNavSDKClient: Searching in project '{project_id}' for: '{query_text[:50]}...'")
        response_data = await self._request("POST", endpoint, json_data=payload)
        # Assuming response_data is like {"results": List[CodeNavSearchResult-like-dicts]}
        return response_data.get("results", []) 

    async def trigger_indexing(self, project_id: str, directory_path_in_container: str) -> Dict[str, Any]:
        """Requests CodeNavAgent to index a directory."""
        endpoint = "/index_project" # Matches /index_project in CodeNavAgent's FastAPI
        payload = {
            "project_id": project_id,
            "directory_path_in_container": directory_path_in_container
        }
        logger.info(f"CodeNavSDKClient: Triggering indexing for project '{project_id}', path '{directory_path_in_container}'")
        return await self._request("POST", endpoint, json_data=payload)

    # Add more methods to interact with other CodeNavAgent API endpoints
