import logging
from typing import TYPE_CHECKING

# ✅ Prevent circular import issues
if TYPE_CHECKING:
    from .models import SDKMCPStrategyRequestContext, SDKMCPStrategyResponse

# --- Client Imports ---
from .build_system import BuildSystemClient
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

# --- Define __all__ explicitly ---
__all__ = [
    "BuildSystemClient",
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
