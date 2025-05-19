import logging
from typing import Optional, Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .client import ForgeIQClient  # ✅ Prevent circular imports

from .exceptions import APIError

logger = logging.getLogger(__name__)

class BuildSystemClient:
    def __init__(self, client: "ForgeIQClient"):  # ✅ Fixed type hint
        """
        Initializes the BuildSystemClient.
        :param client: An instance of ForgeIQClient for making API calls.
        """
        self._client = client  # This is an instance of ForgeIQClient
        logger.info("BuildSystemClient initialized.")

    async def get_project_configuration(self, project_id: str) -> Dict[str, Any]:
        """
        Retrieves the build system configuration for a specific project.
        """
        endpoint = f"/api/forgeiq/projects/{project_id}/build-config"
        logger.info(f"SDK: Getting build system configuration for project '{project_id}'")
        try:
            return await self._client._request("GET", endpoint)
        except Exception as e:
            logger.error(f"Error fetching project configuration for '{project_id}': {e}")
            raise APIError(f"Failed to get project configuration for {project_id}: {e}") from e  # ✅ Improves error handling

    async def get_project_build_graph(self, project_id: str) -> Dict[str, Any]:
        """
        Retrieves the build graph (DAG) for a specific project.
        """
        endpoint = f"/api/forgeiq/projects/{project_id}/build-graph"
        logger.info(f"SDK: Getting build graph for project '{project_id}'")
        try:
            response_data = await self._client._request("GET", endpoint)
            return response_data  # ✅ Kept raw dict for now
        except Exception as e:
            logger.error(f"Error fetching build graph for '{project_id}': {e}")
            raise APIError(f"Failed to get build graph for {project_id}: {e}") from e

    async def list_available_tasks(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Lists available tasks or task types in the system, optionally filtered by project.
        """
        endpoint = "/api/forgeiq/tasks"
        params = {"project_id": project_id} if project_id else {}
        logger.info(f"SDK: Listing available tasks (project: {project_id or 'all'}).")
        try:
            response_data = await self._client._request("GET", endpoint, params=params)
            return response_data.get("tasks", [])  # ✅ Assumes API returns {"tasks": [...]}
        except Exception as e:
            logger.error(f"Error listing available tasks: {e}")
            raise APIError(f"Failed to list available tasks: {e}") from e

    async def get_task_details(self, task_type_or_id: str, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Retrieves details for a specific task type or registered task ID.
        """
        endpoint = f"/api/forgeiq/tasks/{task_type_or_id}"
        params = {"project_id": project_id} if project_id else {}
        logger.info(f"SDK: Getting details for task '{task_type_or_id}' (project: {project_id or 'any'}).")
        try:
            return await self._client._request("GET", endpoint, params=params)
        except Exception as e:
            logger.error(f"Error getting task details for '{task_type_or_id}': {e}")
            raise APIError(f"Failed to get task details for {task_type_or_id}: {e}") from e
