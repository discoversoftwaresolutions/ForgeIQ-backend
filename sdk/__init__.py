import logging
from typing import TYPE_CHECKING

# ✅ Prevent circular imports by delaying initialization
if TYPE_CHECKING:
    from .build_system import BuildSystemClient
    from .models import (
        SDKMCPStrategyRequestContext, SDKMCPStrategyResponse,  # ✅ Importing only if needed
        SDKDagDefinition, SDKDagNode, SDKDagExecutionStatus,
        SDKTaskStatus, SDKDeploymentStatus, SDKFileChange
    )

# --- Core SDK Components ---
from .client import ForgeIQClient
from .exceptions import (
    ForgeIQSDKError, APIError, AuthenticationError,
    NotFoundError, RequestTimeoutError
)
from .models import (
    SDKDagDefinition, SDKDagNode, SDKDagExecutionStatus,
    SDKTaskStatus, SDKDeploymentStatus, SDKFileChange
)

# ✅ Configure logging to prevent "No handler found" warnings
logging.getLogger(__name__).addHandler(logging.NullHandler())

# --- Additional SDK Clients ---
from .debugiq_sdk import DebugIQClient
from .codenav_sdk import CodeNavSDKClient

# --- Define `__all__` explicitly ---
__all__ = [
    "ForgeIQClient",
    "ForgeIQSDKError",
    "APIError",
    "AuthenticationError",
    "NotFoundError",
    "RequestTimeoutError",
    "SDKDagDefinition",
    "SDKDagNode",
    "SDKDagExecutionStatus",
    "SDKTaskStatus",
    "SDKDeploymentStatus",
    "SDKFileChange",
    "DebugIQClient",
    "CodeNavSDKClient",
]
