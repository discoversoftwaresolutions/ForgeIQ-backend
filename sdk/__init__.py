import logging
from typing import TYPE_CHECKING

# ✅ Prevent circular imports by delaying initialization
if TYPE_CHECKING:
    from .build_system import BuildSystemClient
    from .models import SDKMCPStrategyRequestContext, SDKMCPStrategyResponse  # ✅ Keep only if needed

# --- Client Imports ---
from .client import ForgeIQClient
from .exceptions import ForgeIQSDKError, APIError, AuthenticationError, NotFoundError, RequestTimeoutError

# --- Models ---
from .models import (
    SDKDagDefinition, SDKDagNode, SDKDagExecutionStatus,
    SDKTaskStatus, SDKDeploymentStatus, SDKFileChange
)

# Configure a null handler for the library's root logger
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
