import os
import httpx
import logging
from typing import Optional, Dict, Any, List, TYPE_CHECKING

# ✅ Prevent circular imports
if TYPE_CHECKING:
    from sdk.models import CodeNavSearchResult  # ✅ Ensure this exists in sdk/models.py

# ✅ Logger Setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

CODENAV_API_BASE_URL_ENV = "CODE_NAV_AGENT_API_URL"  # Example: http://codenav-agent-on-railway:8001


class CodeNavSDKClient:
    """SDK client for interacting with CodeNavAgent API."""

    def __init__(self, base_url: Optional[str] = None, timeout: int = 60):
        """
        Initializes the CodeNav SDK Client.
        :param base_url: The base URL of the CodeNavAgent API.
        :param timeout: Request timeout in seconds.
        """
        self.base_url = base_url or os.getenv(CODENAV_API_BASE_URL_ENV)

        if not self.base_url:
            msg = f"CodeNavSDKClient: {CODENAV_API_BASE_URL_ENV} not set."
            logger.error(msg)
            raise ValueError(msg)

        try:
            self.http_client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
            logger.info(f"CodeNavSDKClient initialized for base URL: {self.base_url}")
        except Exception as e:
            logger.critical(f"Failed to initialize CodeNavSDKClient HTTP client: {e}", exc_info=True)
            raise RuntimeError(f"CodeNav SDK Client initialization failed: {e}") from e

    async def close(self):
        """Closes the HTTP client."""
        await self.http_client.aclose()
        logger.info("CodeNavSDKClient HTTP client closed.")

    async def _request(self, method: str, endpoint: str, json_data: Optional[Dict] = None) -> Dict[str, Any]:
        """Handles HTTP requests to CodeNavAgent."""
        try:
            logger.debug(f"SDK Request: {method} {endpoint} - JSON: {str(json_data)[:200]}...")
            response = await self.http_client.request(method, endpoint, json=json_data)
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            error_body_text = e.response.text
            logger.error(f"CodeNav API Error: {e.response.status_code} calling {e.request.url}. Response: {error_body_text[:200]}")
            raise RuntimeError(f"API request failed with status {e.response.status_code}: {error_body_text}") from e

        except httpx.RequestError as e:
            logger.error(f"CodeNav Request Error: {e.request.method} {e.request.url} - {e}")
            raise RuntimeError(f"Failed request: {e}") from e

        except Exception as e:
            logger.error(f"Unexpected CodeNav SDK error: {e}", exc_info=True)
            raise RuntimeError(f"Unexpected SDK error: {e}") from e

    async def search_code(self, project_id: str, query_text: str, limit: int = 5, filters: Optional[Dict[str, Any]] = None) -> List["CodeNavSearchResult"]:
        """
        Searches project code using CodeNavAgent.
        :param project_id: The project ID to search within.
        :param query_text: Search query string.
        :param limit: Maximum number of results to return.
        :param filters: Optional filters for narrowing results.
        :return: List of search results.
        """
        endpoint = "/search"
        payload = {
            "project_id": project_id,
            "query_text": query_text,
            "limit": limit,
            "filters": filters or {},
        }

        logger.info(f"CodeNavSDKClient: Searching project '{project_id}' for: '{query_text[:50]}...'")
        response_data = await self._request("POST", endpoint, json_data=payload)
        return response_data.get("results", [])  # ✅ Ensure API returns structured results

    async def trigger_indexing(self, project_id: str, directory_path: str) -> Dict[str, Any]:
        """
        Requests CodeNavAgent to index a project directory.
        :param project_id: The project ID for indexing.
        :param directory_path: Directory path inside the container.
        :return: API response confirming indexing request.
        """
        endpoint = "/index_project"
        payload = {
            "project_id": project_id,
            "directory_path": directory_path,
        }

        logger.info(f"CodeNavSDKClient: Triggering indexing for project '{project_id}', path '{directory_path}'")
        return await self._request("POST", endpoint, json_data=payload)
