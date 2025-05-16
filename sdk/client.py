# =====================
# 📁 sdk/client.py
# =====================
import os
import json # For potential JSON decode errors, good to have if _request handles it
import httpx
import logging
import uuid # <<< Added for generating default request_ids
from typing import Optional, Dict, Any, List

# Assuming these are in the same sdk package and __init__.py exports them
from .exceptions import APIError, AuthenticationError, NotFoundError, RequestTimeoutError, ForgeIQSDKError
from .models import SDKDagDefinition, SDKDagExecutionStatus, SDKDeploymentStatus 

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60

class ForgeIQClient:
    def __init__(self, 
                 base_url: Optional[str] = None, 
                 api_key: Optional[str] = None,
                 timeout: int = DEFAULT_TIMEOUT_SECONDS):
        """
        Initializes the ForgeIQ Client.
        :param base_url: The base URL of the ForgeIQ API. Can also be set via FORGEIQ_API_BASE_URL env var.
        :param api_key: The API key for authentication. Can also be set via FORGEIQ_API_KEY env var.
        :param timeout: Request timeout in seconds.
        """
        self.base_url = base_url or os.getenv("FORGEIQ_API_BASE_URL")
        self.api_key = api_key or os.getenv("FORGEIQ_API_KEY")

        if not self.base_url:
            # Log error and raise for clarity, as client is unusable
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
        """Closes the underlying HTTP client resources."""
        if hasattr(self, 'http_client') and self.http_client:
            await self.http_client.aclose()
            logger.info("ForgeIQClient HTTP client closed.")

    async def _request(self, method: str, endpoint: str, json_data: Optional[Dict] = None, params: Optional[Dict] = None) -> Dict[str, Any]:
        """Internal helper for making API requests."""
        if not self.http_client:
             err_msg = "HTTP client not initialized in ForgeIQClient."
             logger.error(err_msg)
             raise ForgeIQSDKError(err_msg)
        try:
            logger.debug(f"SDK Request: {method} {endpoint} - Params: {params} - JSON: {str(json_data)[:200]}...") # Log request details
            response = await self.http_client.request(method, endpoint, json=json_data, params=params)
            response.raise_for_status() 
            return response.json()
        except httpx.HTTPStatusError as e:
            error_body_text = e.response.text
            logger.error(f"API Error: {e.response.status_code} calling {e.request.url}. Response: {error_body_text[:500]}") # Log more of body
            if e.response.status_code == 401:
                raise AuthenticationError(status_code=e.response.status_code, error_body=error_body_text)
            elif e.response.status_code == 403: # Often also auth related or permission
                raise AuthenticationError(message="Forbidden. Check permissions or authentication.", status_code=e.response.status_code, error_body=error_body_text)
            elif e.response.status_code == 404:
                raise NotFoundError(status_code=e.response.status_code, error_body=error_body_text)
            else:
                raise APIError(message=f"API request failed: {e.response.status_code}", status_code=e.response.status_code, error_body=error_body_text) from e
        except httpx.TimeoutException as e:
            logger.error(f"Request Timeout: {e.request.method} {e.request.url}")
            raise RequestTimeoutError(message=f"Request to {e.request.url} timed out.") from e
        except httpx.RequestError as e: 
            logger.error(f"Request Error: {e.request.method} {e.request.url} - {e}")
            raise APIError(message=f"Request failed: {e}") from e
        except json.JSONDecodeError as e: # Catch if response.json() fails
            logger.error(f"JSON Decode Error: Could not parse response from {endpoint}. Error: {e}. Response text: {e.doc[:500] if hasattr(e, 'doc') else 'N/A'}")
            raise ForgeIQSDKError(message=f"Failed to parse JSON response from {endpoint}: {e}") from e
        except Exception as e: # Catch-all for other unexpected errors
            logger.error(f"Unexpected SDK error during request to {endpoint}: {e}", exc_info=True)
            raise ForgeIQSDKError(message=f"An unexpected error occurred: {e}") from e


    async def submit_pipeline_prompt(self, 
                                     project_id: str, 
                                     user_prompt: str, 
                                     additional_context: Optional[Dict[str, Any]] = None,
                                     request_id: Optional[str] = None
                                    ) -> Dict[str, Any]:
        """
        Submits a user prompt to generate a pipeline DAG.
        Assumes an API endpoint like /api/forgeiq/pipelines/generate exists.
        """
        endpoint = "/api/forgeiq/pipelines/generate" 
        payload = {
            "request_id": request_id or str(uuid.uuid4()),
            "project_id": project_id,
            "user_prompt_data": { # This structure should match what the backend expects
                "prompt_text": user_prompt,
                "target_project_id": project_id, # Often redundant if project_id is top-level
                "additional_context": additional_context or {}
            }
        }
        logger.info(f"SDK: Submitting pipeline prompt for project '{project_id}' (Req ID: {payload['request_id']})")
        return await self._request("POST", endpoint, json_data=payload)

    async def get_dag_execution_status(self, project_id: str, dag_id: str) -> SDKDagExecutionStatus:
        """Retrieves the execution status of a specific DAG."""
        endpoint = f"/api/forgeiq/projects/{project_id}/dags/{dag_id}/status"
        logger.info(f"SDK: Getting DAG execution status for project '{project_id}', DAG '{dag_id}'")
        response_data = await self._request("GET", endpoint)
        # For robustness, validate response_data against SDKDagExecutionStatus structure here
        # Pydantic models would do this automatically if used for response_model in API and SDK
        return SDKDagExecutionStatus(**response_data)


    async def trigger_deployment(self, 
                               project_id: str, 
                               service_name: str, 
                               commit_sha: str, 
                               target_environment: str,
                               request_id: Optional[str] = None
                              ) -> Dict[str, Any]:
        """Requests a deployment for a specific service and commit."""
        endpoint = "/api/forgeiq/deployments/trigger"
        payload = {
            "request_id": request_id or str(uuid.uuid4()),
            "project_id": project_id,
            "service_name": service_name,
            "commit_sha": commit_sha,
            "target_environment": target_environment,
            "triggered_by": "PythonSDK"
        }
        logger.info(f"SDK: Requesting deployment for service '{service_name}' in project '{project_id}' to env '{target_environment}' (Req ID: {payload['request_id']})")
        return await self._request("POST", endpoint, json_data=payload)

    async def get_deployment_status(self, project_id: str, service_name: str, deployment_request_id: str) -> SDKDeploymentStatus:
        """Retrieves the status of a specific deployment request."""
        endpoint = f"/api/forgeiq/projects/{project_id}/services/{service_name}/deployments/{deployment_request_id}/status"
        logger.info(f"SDK: Getting deployment status for request ID '{deployment_request_id}'")
        response_data = await self._request("GET", endpoint)
        # Validate/map to SDKDeploymentStatus
        return SDKDeploymentStatus(**response_data)
    ```
