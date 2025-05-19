import os
import json
import httpx
import logging
import uuid
from typing import Optional, Dict, Any, List, TYPE_CHECKING
from sdk.models import SDKAlgorithmContext  # ✅ Ensure this is correct

# ✅ Prevent circular imports by delaying initialization
if TYPE_CHECKING:
    from .hooks import HookManager, DeployContext
    from .models import SDKAlgorithmContext, SDKOptimizedAlgorithmResponse, SDKDagDefinition, SDKDagExecutionStatus, SDKDeploymentStatus

# --- Core SDK Imports ---
from .exceptions import (
    APIError, AuthenticationError, NotFoundError,
    RequestTimeoutError, ForgeIQSDKError
)

# ✅ Logger Setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEFAULT_TIMEOUT_SECONDS = 60


class ForgeIQClient:
    """SDK client for interacting with ForgeIQ-backend services."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        hook_manager: Optional["HookManager"] = None,
    ):
        """
        Initializes the ForgeIQ Client.
        :param base_url: The base URL of the ForgeIQ API.
        :param api_key: The API key for authentication.
        :param timeout: Request timeout in seconds.
        :param hook_manager: Optional instance of HookManager for client-side hooks.
        """
        self.base_url = base_url or os.getenv("FORGEIQ_API_BASE_URL")
        self.api_key = api_key or os.getenv("FORGEIQ_API_KEY")
        self.hooks = hook_manager if hook_manager is not None else HookManager()

        if not self.base_url:
            err_msg = "ForgeIQ API base_url must be provided or set via FORGEIQ_API_BASE_URL environment variable."
            logger.critical(err_msg)
            raise ValueError(err_msg)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            logger.warning("FORGEIQ_API_KEY not set. Client will make unauthenticated requests if API allows.")

        try:
            self.http_client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout)
            logger.info(f"ForgeIQClient initialized for base URL: {self.base_url}")
        except Exception as e:
            logger.critical(f"Failed to initialize httpx.AsyncClient for ForgeIQClient: {e}", exc_info=True)
            raise ForgeIQSDKError(f"HTTP Client initialization error: {e}") from e

    async def close(self):
        """Closes the HTTP client."""
        if hasattr(self, "http_client") and self.http_client:
            await self.http_client.aclose()
            logger.info("ForgeIQClient HTTP client closed.")

    async def _request(
        self, method: str, endpoint: str, json_data: Optional[Dict] = None, params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Handles HTTP requests."""
        if not self.http_client:
            err_msg = "HTTP client not initialized in ForgeIQClient."
            logger.error(err_msg)
            raise ForgeIQSDKError(err_msg)

        try:
            logger.debug(f"SDK Request: {method} {endpoint} - Params: {params} - JSON: {str(json_data)[:200]}...")
            response = await self.http_client.request(method, endpoint, json=json_data, params=params)
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            error_body_text = e.response.text
            logger.error(f"API Error: {e.response.status_code} calling {e.request.url}. Response: {error_body_text[:500]}")
            if e.response.status_code == 401:
                raise AuthenticationError(status_code=e.response.status_code, error_body=error_body_text)
            elif e.response.status_code == 403:
                raise AuthenticationError(message="Forbidden.", status_code=e.response.status_code, error_body=error_body_text)
            elif e.response.status_code == 404:
                raise NotFoundError(status_code=e.response.status_code, error_body=error_body_text)
            else:
                raise APIError(
                    message=f"API request failed: {e.response.status_code}", status_code=e.response.status_code, error_body=error_body_text
                ) from e

        except httpx.TimeoutException as e:
            logger.error(f"Request Timeout: {e.request.method} {e.request.url}")
            raise RequestTimeoutError(message=f"Request to {e.request.url} timed out.") from e
        except httpx.RequestError as e:
            logger.error(f"Request Error: {e.request.method} {e.request.url} - {e}")
            raise APIError(message=f"Request failed: {e}") from e
        except json.JSONDecodeError as e:
            logger.error(f"JSON Decode Error: {e}. Response text: {e.doc[:500] if hasattr(e, 'doc') else 'N/A'}")
            raise ForgeIQSDKError(message=f"Failed to parse JSON response: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected SDK error: {e}", exc_info=True)
            raise ForgeIQSDKError(message=f"An unexpected error occurred: {e}") from e

    async def submit_pipeline_prompt(
        self, project_id: str, user_prompt: str, additional_context: Optional[Dict[str, Any]] = None, request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Submits a pipeline prompt to generate a DAG.
        """
        endpoint = "/api/forgeiq/pipelines/generate"
        payload = {
            "request_id": request_id or str(uuid.uuid4()),
            "project_id": project_id,
            "user_prompt_data": {
                "prompt_text": user_prompt,
                "target_project_id": project_id,
                "additional_context": additional_context or {},
            },
        }

        logger.info(f"SDK: Submitting pipeline prompt for project '{project_id}' (Req ID: {payload['request_id']})")
        return await self._request("POST", endpoint, json_data=payload)

    async def request_build_strategy_optimization(self, context: SDKAlgorithmContext) -> SDKOptimizedAlgorithmResponse:
        """Requests optimization of a build strategy."""
        endpoint = f"/api/forgeiq/projects/{context['project_id']}/build-strategy/optimize"
        logger.info(f"SDK: Requesting build strategy optimization for project '{context['project_id']}'")

        payload = {"dag_representation": context["dag_representation"], "telemetry_data": context["telemetry_data"]}
        response_data = await self._request("POST", endpoint, json_data=payload)
        return SDKOptimizedAlgorithmResponse(**response_data)

    async def request_mcp_build_strategy(self, project_id: str, current_dag_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Requests an MCP build strategy optimization."""
        endpoint = f"/api/forgeiq/mcp/optimize-strategy/{project_id}"
        payload = {"current_dag_info": current_dag_info}
        logger.info(f"SDK: Requesting MCP build strategy optimization for project '{project_id}'")
        return await self._request("POST", endpoint, json_data=payload)
from typing import TypedDict, List, Dict, Any

class SDKAlgorithmContext(TypedDict):
    """Defines the request structure for proprietary algorithm execution."""
    project_id: str
    dag_representation: List[Any]
    telemetry_data: Dict[str, Any]
from typing import TypedDict, Optional

class SDKOptimizedAlgorithmResponse(TypedDict):
    """Defines the response structure for build strategy optimization."""
    algorithm_reference: str
    benchmark_score: float
    generated_code_or_dag: Optional[str]
    message: Optional[str]
