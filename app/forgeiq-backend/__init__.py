# =====================================================================
# 📁 app/forgeiq-backend/__init__.py (Package Initialization)
# =====================================================================
import sys, os
sys.path.append(os.path.dirname(__file__))  # ✅ Adds 'forgeiq-backend' to Python’s lookup paths
# ✅ Explicitly defining available exports for controlled imports
__all__ = [
    "auth",
    "api_models",
    "mcp",
    "algorithm",
    "index"
]

# ✅ Importing key modules to ensure package accessibility
from .auth import get_private_intel_client, get_api_key
from .api_models import (
    UserPromptData,
    PipelineGenerateRequest,
    DeploymentTriggerRequest,
    PipelineGenerateResponse,
    DeploymentTriggerResponse,
    SDKTaskStatusModel,
    SDKDagExecutionStatusModel,
    SDKDeploymentStatusModel,
    ProjectConfigResponse,
    BuildGraphNodeModel,
    BuildGraphResponse,
    TaskDefinitionModel,
    TaskListResponse,
    ApplyAlgorithmRequest,
    ApplyAlgorithmResponse,
    MCPStrategyApiRequest,
    MCPStrategyApiResponse,
    MCPStrategyApiDetails
)
from .mcp import MCPProcessor
from .algorithm import AlgorithmExecutor
from .index import TASK_COMMANDS
