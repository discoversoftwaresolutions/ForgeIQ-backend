# =====================
# 📁 sdk/client.py
# =====================
import os
import json
import httpx
import logging
import uuid
from typing import Optional, Dict, Any, List
from sdk.models import SDKAlgorithmContext  # ✅ Ensure this exists
from .exceptions import APIError, AuthenticationError, NotFoundError, RequestTimeoutError, ForgeIQSDKError
from .models import SDKDagDefinition, SDKDagExecutionStatus, SDKDeploymentStatus 
from .hooks import HookManager, DeployContext # <<< IMPORT HookManager and DeployContext

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60

class ForgeIQClient:
    def __init__(self, 
                 base_url: Optional[str] = None, 
                 api_key: Optional[str] = None,
                 timeout: int = DEFAULT_TIMEOUT_SECONDS,
                 hook_manager: Optional[HookManager] = None): # <<< ADDED hook_manager
        """
        Initializes the ForgeIQ Client.
        :param base_url: The base URL of the ForgeIQ API.
        :param api_key: The API key for authentication.
        :param timeout: Request timeout in seconds.
        :param hook_manager: Optional instance of HookManager for client-side hooks.
        """
        self.base_url = base_url or os.getenv("FORGEIQ_API_BASE_URL")
        self.api_key = api_key or os.getenv("FORGEIQ_API_KEY")
        self.hooks = hook_manager if hook_manager is not None else HookManager() # <<< INITIALIZE HOOKS

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
        if hasattr(self, 'http_client') and self.http_client:
            await self.http_client.aclose()
            logger.info("ForgeIQClient HTTP client closed.")

    async def _request(self, method: str, endpoint: str, json_data: Optional[Dict] = None, params: Optional[Dict] = None) -> Dict[str, Any]:
        # ... (This method remains the same as in response #59) ...
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
            error_body_text = e.response.text; logger.error(f"API Error: {e.response.status_code} calling {e.request.url}. Response: {error_body_text[:500]}")
            if e.response.status_code == 401: raise AuthenticationError(status_code=e.response.status_code, error_body=error_body_text)
            elif e.response.status_code == 403: raise AuthenticationError(message="Forbidden.", status_code=e.response.status_code, error_body=error_body_text)
            elif e.response.status_code == 404: raise NotFoundError(status_code=e.response.status_code, error_body=error_body_text)
            else: raise APIError(message=f"API request failed: {e.response.status_code}", status_code=e.response.status_code, error_body=error_body_text) from e
        except httpx.TimeoutException as e: logger.error(f"Request Timeout: {e.request.method} {e.request.url}"); raise RequestTimeoutError(message=f"Request to {e.request.url} timed out.") from e
        except httpx.RequestError as e: logger.error(f"Request Error: {e.request.method} {e.request.url} - {e}"); raise APIError(message=f"Request failed: {e}") from e
        except json.JSONDecodeError as e: logger.error(f"JSON Decode Error: {e}. Response text: {e.doc[:500] if hasattr(e, 'doc') else 'N/A'}"); raise ForgeIQSDKError(message=f"Failed to parse JSON response: {e}") from e
        except Exception as e: logger.error(f"Unexpected SDK error: {e}", exc_info=True); raise ForgeIQSDKError(message=f"An unexpected error occurred: {e}") from e


    async def submit_pipeline_prompt(self, 
                                     project_id: str, 
                                     user_prompt: str, 
                                     additional_context: Optional[Dict[str, Any]] = None,
                                     request_id: Optional[str] = None
                                    ) -> Dict[str, Any]:
        # ... (This method remains the same as in response #59, using uuid.uuid4()) ...
        endpoint = "/api/forgeiq/pipelines/generate" 
        payload = {
            "request_id": request_id or str(uuid.uuid4()),
            "project_id": project_id,
            "user_prompt_data": {
                "prompt_text": user_prompt,
                "target_project_id": project_id,
                "additional_context": additional_context or {}
            }
        }
        logger.info(f"SDK: Submitting pipeline prompt for project '{project_id}' (Req ID: {payload['request_id']})")
        return await self._request("POST", endpoint, json_data=payload)


    async def get_dag_execution_status(self, project_id: str, dag_id: str) -> SDKDagExecutionStatus:
        # ... (This method remains the same as in response #59) ...
        endpoint = f"/api/forgeiq/projects/{project_id}/dags/{dag_id}/status"
        logger.info(f"SDK: Getting DAG execution status for project '{project_id}', DAG '{dag_id}'")
        response_data = await self._request("GET", endpoint)
        return SDKDagExecutionStatus(**response_data)


    async def trigger_deployment(self, 
                               project_id: str, 
                               service_name: str, 
                               commit_sha: str, 
                               target_environment: str,
                               request_id: Optional[str] = None,
                               # Pass through other context for hooks if needed
                               **hook_context_kwargs: Any 
                              ) -> Dict[str, Any]:
        """
        Requests a deployment for a specific service and commit.
        Executes before_deploy hooks if any are registered.
        """
        logger.info(f"SDK: Preparing to trigger deployment for service '{service_name}' in project '{project_id}' to env '{target_environment}'.")

        # --- Execute Before-Deploy Hooks ---
        deploy_context_data = {
            "project_id": project_id,
            "service_name": service_name,
            "commit_sha": commit_sha,
            "target_environment": target_environment,
            "request_id": request_id or str(uuid.uuid4()) # Generate if not provided for hook context
        }
        deploy_context_data.update(hook_context_kwargs) # Add any extra context
        context = DeployContext(**deploy_context_data) # Use the TypedDict for context

        if not await self.hooks.execute_before_deploy_hooks(context):
            hook_halt_msg = "Deployment halted by a before_deploy hook."
            logger.warning(hook_halt_msg)
            # Raising a specific error allows SDK users to catch it.
            raise ForgeIQSDKError(hook_halt_msg, status_code=403) # 403 Forbidden as an example status

        # --- Proceed with API Call if hooks passed ---
        logger.info(f"SDK: All before_deploy hooks passed. Proceeding with deployment trigger for request ID '{context['request_id']}'.")
        endpoint = "/api/forgeiq/deployments/trigger"
        payload = {
            "request_id": context['request_id'], # Use the (potentially generated) ID from context
            "project_id": project_id,
            "service_name": service_name,
            "commit_sha": commit_sha,
            "target_environment": target_environment,
            "triggered_by": "PythonSDK"
        }
        return await self._request("POST", endpoint, json_data=payload)

    async def get_deployment_status(self, project_id: str, service_name: str, deployment_request_id: str) -> SDKDeploymentStatus:
        # ... (This method remains the same as in response #59) ...
        endpoint = f"/api/forgeiq/projects/{project_id}/services/{service_name}/deployments/{deployment_request_id}/status"
        logger.info(f"SDK: Getting deployment status for request ID '{deployment_request_id}'")
        response_data = await self._request("GET", endpoint)
        return SDKDeploymentStatus(**response_data)
    
# In sdk/client.py, within ForgeIQClient class:
# Ensure SDKAlgorithmContext and SDKOptimizedAlgorithmResponse are imported from .models

async def request_build_strategy_optimization(
    self,
    context: SDKAlgorithmContext # Use the TypedDict/Pydantic model
) -> SDKOptimizedAlgorithmResponse:
    """
    Requests optimization of a build strategy for a project via ForgeIQ-backend,
    which in turn calls the private AlgorithmAgent.
    """
    endpoint = f"/api/forgeiq/projects/{context['project_id']}/build-strategy/optimize" # NEW API Endpoint
    logger.info(f"SDK: Requesting build strategy optimization for project '{context['project_id']}'")

    # The payload for the backend should match what its Pydantic model expects.
    # It might just pass through the context or parts of it.
    payload = {
        "dag_representation": context["dag_representation"],
        "telemetry_data": context["telemetry_data"]
    }
    response_data = await self._request("POST", endpoint, json_data=payload)
    # Assume response_data matches SDKOptimizedAlgorithmResponse
    return SDKOptimizedAlgorithmResponse(**response_data) # type: ignore
# In ForgeIQClient class (sdk/client.py)
async def request_mcp_build_strategy(self, project_id: str, current_dag_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    endpoint = f"/api/forgeiq/mcp/optimize-strategy/{project_id}"
    payload = {"current_dag_info": current_dag_info} # MCP might need current DAG
    logger.info(f"SDK: Requesting MCP build strategy optimization for project '{project_id}'")
    return await self._request("POST", endpoint, json_data=payload)
# =================================================================
# 📁 sdk/client.py (additions to ForgeIQClient class)
# =================================================================
# ... (ensure these imports are at the top of client.py)
# from .models import SDKMCPStrategyRequestContext, SDKMCPStrategyResponse
# import logging
# logger = logging.getLogger(__name__) # if not already defined
# ...

# Inside ForgeIQClient class:
async def request_mcp_build_strategy(
    self,
    project_id: str,
    current_dag_snapshot: Optional[List[Dict[str, Any]]] = None,
    optimization_goal: Optional[str] = None,
    additional_mcp_context: Optional[Dict[str, Any]] = None
) -> SDKMCPStrategyResponse: # Using SDK's TypedDict for return type
    """
    Requests a build strategy optimization from the private MCP
    via the ForgeIQ-backend.
    """
    endpoint = f"/api/forgeiq/mcp/optimize-strategy/{project_id}" # Matches backend API

    # Payload for the backend API, matching MCPStrategyApiRequest Pydantic model
    payload = {
        "current_dag_snapshot": current_dag_snapshot,
        "optimization_goal": optimization_goal,
        "additional_mcp_context": additional_mcp_context
    }

    logger.info(f"SDK: Requesting MCP build strategy optimization for project '{project_id}'. Goal: {optimization_goal or 'default'}")

    response_data = await self._request("POST", endpoint, json_data=payload)

    # Assuming response_data from backend matches SDKMCPStrategyResponse structure
    # Pydantic models in the backend handle the exact API contract.
    # The SDK might just pass through the dict or could re-validate with TypedDicts/Pydantic.
    return SDKMCPStrategyResponse(**response_data) #type: ignore
# =================================================================
# FILE: sdk/client.py (Showing additions/updates to ForgeIQClient)
# =================================================================
import logging # Ensure this is at the top of your sdk/client.py
import uuid # Ensure this is at the top if other methods use it for default IDs
from typing import Optional, Dict, Any, List # Ensure these are at the top

# Assuming these are correctly defined in your sdk/exceptions.py and sdk/models.py
from .exceptions import APIError, AuthenticationError, NotFoundError, RequestTimeoutError, ForgeIQSDKError
from .models import (
    SDKDagDefinition, SDKDagExecutionStatus, SDKDeploymentStatus,
    # Add the new response type for apply_proprietary_algorithm if you define one:
    # SDKApplyAlgorithmResponse # Example
)
# import httpx # httpx is used by _request, ensure it's imported where _request is defined or http_client is init

# Get a logger for the SDK client module
logger = logging.getLogger(__name__) # Standard practice for library logging


class ForgeIQClient:
    # ... (existing __init__, close, _request, and other SDK methods) ...

    async def apply_proprietary_algorithm(
        self,
        algorithm_id: str,
        context_data: Dict[str, Any],
        project_id: Optional[str] = None
    ) -> Dict[str, Any]: # For V0.1, returning a Dict. Ideally, map to a specific SDK model.
        """
        Invokes a named proprietary algorithm via the ForgeIQ-backend.

        :param algorithm_id: The ID of the proprietary algorithm to run (e.g., "CABGP", "RBCP").
        :param context_data: Input data and context required by the algorithm.
        :param project_id: Optional project ID if the algorithm is project-specific.
        :return: A dictionary containing the result from the algorithm execution.
                 It's recommended to define a specific SDK model (e.g., SDKApplyAlgorithmResponse)
                 in sdk/models.py for this response for better type safety.
        :raises APIError: If the backend API returns an error.
        :raises ForgeIQSDKError: For other SDK-level errors.
        """
        endpoint = "/api/forgeiq/algorithms/apply" # Matches the public ForgeIQ-backend endpoint
        payload = {
            "algorithm_id": algorithm_id,
            "project_id": project_id,
            "context_data": context_data
        }
        
        logger.info(
            f"SDK: Requesting application of proprietary algorithm '{algorithm_id}'"
            f" for project '{project_id if project_id else 'N/A'}'."
        )
        logger.debug(f"SDK: Payload for '{algorithm_id}': {str(payload)[:200]}...") # Log snippet of payload

        # self._request is assumed to be an existing async method in ForgeIQClient
        # that handles the actual httpx call, error handling, and JSON parsing.
        try:
            response_data = await self._request("POST", endpoint, json_data=payload)
            # If you have an SDKApplyAlgorithmResponse model:
            # from .models import SDKApplyAlgorithmResponse
            # return SDKApplyAlgorithmResponse(**response_data)
            return response_data # Return raw dict for now
        except Exception as e:
            logger.error(f"SDK: Failed to apply proprietary algorithm '{algorithm_id}': {e}", exc_info=True)
            # Re-raise the original error if it's an SDK-defined one, or wrap it
            if isinstance(e, ForgeIQSDKError):
                raise
            raise ForgeIQSDKError(f"Failed to apply algorithm '{algorithm_id}': {str(e)}") from e
from typing import TypedDict, List, Dict, Any

class SDKAlgorithmContext(TypedDict):
    project_id: str
    dag_representation: List[Any]
    telemetry_data: Dict[str, Any]
    # ... (other existing SDK methods like submit_pipeline_prompt, generate_code_via_api, etc.) ...
