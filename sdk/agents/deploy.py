# ==============================
# 📁 sdk/agents/deploy.py
# ==============================
import logging
from typing import Optional, Dict, Any

# from ..client import ForgeIQClient
# from ..models import SDKDeploymentStatus

logger = logging.getLogger(__name__)

class DeployClient:
    def __init__(self, client): # client would be an instance of ForgeIQClient
        """
        Initializes the DeployClient.
        :param client: An instance of ForgeIQClient for making API calls.
        """
        self._client = client
        logger.info("DeployClient initialized.")

    async def deploy_service(self, 
                           project_id: str, 
                           service_name: str, 
                           commit_sha: str, 
                           target_environment: str,
                           request_id: Optional[str] = None
                          ) -> Dict[str, Any]:
        """
        Requests a deployment for a specific service. Wraps the main client's method.
        """
        logger.info(f"DeployClient: Deploying service '{service_name}' in project '{project_id}' to env '{target_environment}'.")
        # This will use the method defined in the main ForgeIQClient
        return await self._client.trigger_deployment(
            project_id=project_id,
            service_name=service_name,
            commit_sha=commit_sha,
            target_environment=target_environment,
            request_id=request_id
        )

    async def get_deployment_status(self, project_id: str, service_name: str, deployment_request_id: str) -> Dict[str, Any]: # SDKDeploymentStatus
        """
        Retrieves the status of a specific deployment request. Wraps the main client's method.
        """
        logger.info(f"DeployClient: Getting deployment status for request ID '{deployment_request_id}'.")
        # This will use the method defined in the main ForgeIQClient
        return await self._client.get_deployment_status(
            project_id=project_id,
            service_name=service_name,
            deployment_request_id=deployment_request_id
        )
