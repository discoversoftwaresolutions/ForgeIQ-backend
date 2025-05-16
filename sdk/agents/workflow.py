# ================================
# 📁 sdk/agents/workflow.py
# ================================
import logging
from typing import Optional, Dict, Any, List

# Assuming ForgeIQClient would be the main entry point for API calls
# from ..client import ForgeIQClient # Relative import if used within the SDK package
# from ..models import SDKDagDefinition, SDKDagExecutionStatus # Relative import

logger = logging.getLogger(__name__)

class WorkflowClient:
    def __init__(self, client): # client would be an instance of ForgeIQClient
        """
        Initializes the WorkflowClient.
        :param client: An instance of ForgeIQClient for making API calls.
        """
        self._client = client
        logger.info("WorkflowClient initialized.")

    async def submit_dag_generation_prompt(self, 
                                         project_id: str, 
                                         user_prompt: str, 
                                         additional_context: Optional[Dict[str, Any]] = None,
                                         request_id: Optional[str] = None
                                        ) -> Dict[str, Any]:
        """
        Submits a prompt to generate a DAG for a project.
        This wraps the ForgeIQClient's method for a more focused interface.
        """
        logger.info(f"WorkflowClient: Submitting DAG generation prompt for project '{project_id}'.")
        # This will use the method defined in the main ForgeIQClient
        return await self._client.submit_pipeline_prompt(
            project_id=project_id,
            user_prompt=user_prompt,
            additional_context=additional_context,
            request_id=request_id
        )

    async def get_dag_status(self, project_id: str, dag_id: str) -> Dict[str, Any]: # Should return SDKDagExecutionStatus
        """
        Retrieves the execution status of a specific DAG.
        """
        logger.info(f"WorkflowClient: Getting status for DAG '{dag_id}' in project '{project_id}'.")
        # This will use the method defined in the main ForgeIQClient
        # The type hint SDKDagExecutionStatus comes from sdk.models
        # For now, returning Dict as the client method returns Dict[str, Any] which should then be parsed
        return await self._client.get_dag_execution_status(project_id=project_id, dag_id=dag_id)

    async def list_project_dags(self, project_id: str) -> List[Dict[str, Any]]: # List[SDKDagDefinition]
        """
        Lists all DAG definitions for a given project.
        (This endpoint would need to be defined in ForgeIQ-backend)
        """
        logger.info(f"WorkflowClient: Listing DAGs for project '{project_id}'.")
        # Example API call structure
        # endpoint = f"/api/forgeiq/projects/{project_id}/dags"
        # return await self._client._request("GET", endpoint)
        logger.warning("list_project_dags not fully implemented in SDK V0.1 - backend endpoint pending.")
        raise NotImplementedError("list_project_dags backend endpoint is not yet defined.")
        # return [] # Placeholder

# How it might be used by an SDK user:
# from forgeiq_sdk import ForgeIQClient # Assuming ForgeIQClient is exposed via sdk/__init__.py
# from forgeiq_sdk.agents.workflow import WorkflowClient
#
# async def main():
#     client = ForgeIQClient(base_url="http://localhost:3002") # Example
#     workflow_ops = WorkflowClient(client)
#     try:
#         # result = await workflow_ops.submit_dag_generation_prompt("proj1", "build and test my app")
#         # print(result)
#         # status = await workflow_ops.get_dag_status("proj1", "dag_123")
#         # print(status)
#         pass
#     finally:
#         await client.close()
#
# if __name__ == "__main__":
#     # import asyncio
#     # asyncio.run(main())
#     pass
