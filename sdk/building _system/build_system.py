# =================================
# 📁 sdk/build_system.py
# =================================
import logging
from typing import Optional, Dict, Any, List
from .build_system import BuildSystemClient
# To use ForgeIQClient for making requests, it needs to be imported.
# This assumes client.py is in the same sdk package.
# from .client import ForgeIQClient # If used directly
# from .exceptions import APIError # If used directly
# from .models import ... # Define any specific response models if needed

logger = logging.getLogger(__name__)

class BuildSystemClient:
    def __init__(self, client): # client would be an instance of ForgeIQClient
        """
        Initializes the BuildSystemClient.
        :param client: An instance of ForgeIQClient for making API calls.
        """
        self._client = client # This is an instance of ForgeIQClient
        logger.info("BuildSystemClient initialized.")

    async def get_project_configuration(self, project_id: str) -> Dict[str, Any]:
        """
        Retrieves the build system configuration for a specific project.
        This would call an API endpoint on ForgeIQ-backend.
        The backend, in turn, might read from buildsystem_config.py or other sources.
        """
        endpoint = f"/api/forgeiq/projects/{project_id}/build-config" # Example endpoint
        logger.info(f"SDK: Getting build system configuration for project '{project_id}'")
        # The _request method is part of ForgeIQClient, so we use self._client._request
        # This assumes ForgeIQClient is passed in and has _request method.
        try:
            return await self._client._request("GET", endpoint)
        except Exception as e:
            logger.error(f"Error fetching project configuration for '{project_id}': {e}")
            # Re-raise or return a default/error structure
            # from .exceptions import APIError (if you want to use SDK's custom exceptions)
            # raise APIError(f"Failed to get project configuration for {project_id}: {e}") from e
            raise # Re-raise for now


    async def get_project_build_graph(self, project_id: str) -> Dict[str, Any]: # Should return structured graph (e.g., SDKDagDefinition)
        """
        Retrieves the build graph (DAG) for a specific project.
        This would call an API endpoint on ForgeIQ-backend.
        The backend might use core.build_graph to provide this.
        """
        endpoint = f"/api/forgeiq/projects/{project_id}/build-graph" # Example endpoint
        logger.info(f"SDK: Getting build graph for project '{project_id}'")
        try:
            response_data = await self._client._request("GET", endpoint)
            # Ideally, validate/map response_data to a defined SDKDagDefinition model
            # from .models import SDKDagDefinition
            # return SDKDagDefinition(**response_data)
            return response_data # Returning raw dict for now
        except Exception as e:
            logger.error(f"Error fetching build graph for '{project_id}': {e}")
            raise

    async def list_available_tasks(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Lists available tasks or task types in the system, optionally filtered by project.
        This would call an API endpoint on ForgeIQ-backend.
        """
        endpoint = "/api/forgeiq/tasks"
        params = {"project_id": project_id} if project_id else {}
        logger.info(f"SDK: Listing available tasks (project: {project_id or 'all'}).")
        try:
            response_data = await self._client._request("GET", endpoint, params=params)
            # response_data should be a list of task definitions
            return response_data.get("tasks", []) # Assuming API returns {"tasks": [...]}
        except Exception as e:
            logger.error(f"Error listing available tasks: {e}")
            raise

    async def get_task_details(self, task_type_or_id: str, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Retrieves details for a specific task type or registered task ID.
        """
        # This endpoint needs to be designed in ForgeIQ-backend
        endpoint = f"/api/forgeiq/tasks/{task_type_or_id}" 
        params = {"project_id": project_id} if project_id else {}
        logger.info(f"SDK: Getting details for task '{task_type_or_id}' (project: {project_id or 'any'}).")
        try:
            return await self._client._request("GET", endpoint, params=params)
        except Exception as e: # Specifically handle NotFoundError from _request if possible
            # from .exceptions import NotFoundError
            # if isinstance(e, NotFoundError):
            #    logger.warning(f"Task '{task_type_or_id}' not found.")
            #    return None
            logger.error(f"Error getting task details for '{task_type_or_id}': {e}")
            raise

# Example of how this BuildSystemClient might be accessed through ForgeIQClient:
#
# In sdk/client.py:
#
# from .build_system import BuildSystemClient # Add this import
#
# class ForgeIQClient:
#     def __init__(self, ...):
#         # ... existing init ...
#         self._build_system_client = None # Lazy load
#
#     @property
#     def build_system(self) -> BuildSystemClient:
#         if self._build_system_client is None:
#             self._build_system_client = BuildSystemClient(self) # Pass self (ForgeIQClient instance)
#         return self._build_system_client
#
# Then SDK users can do:
# client = ForgeIQClient(...)
# config = await client.build_system.get_project_configuration("my_project")
