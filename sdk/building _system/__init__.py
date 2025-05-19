# =======================
# 📁 sdk/__init__.py
# =======================

import logging  # Good practice to configure a null handler for library logs by default

# --- Client Imports ---
from .build_system import BuildSystemClient  # ✅ Fixed spacing issue
from .client import ForgeIQClient
from .exceptions import ForgeIQSDKError, APIError, AuthenticationError, NotFoundError, RequestTimeoutError

# --- Models ---
from .models import (
    SDKDagDefinition, SDKDagNode, SDKDagExecutionStatus,
    SDKTaskStatus, SDKDeploymentStatus, SDKFileChange
)

# Configure a null handler for the library's root logger
# This prevents a "No handler found" warning if the consuming application
# doesn't configure logging, while still allowing the app to set up its own handlers.
logging.getLogger(__name__).addHandler(logging.NullHandler())

# --- Additional SDK Clients ---
from .debugiq_sdk import DebugIQClient
from .codenav_sdk import CodeNavSDKClient
# from .agent_client import GenericAgentClient  # Uncomment if needed


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
    "SDKMCPStrategyRequestContext",
    "SDKMCPStrategyResponse",
]
