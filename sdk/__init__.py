
# =======================
# 📁 sdk/__init__.py
# =======================
import logging # Good practice to configure a null handler for library logs by default
from .build_system import BuildSystemClient # Add thisfrom .client import ForgeIQClient
from .exceptions import ForgeIQSDKError, APIError, AuthenticationError, NotFoundError, RequestTimeoutError
from .models import ( # Export key models users might need for type hinting or construction
    SDKDagDefinition, SDKDagNode, SDKDagExecutionStatus, 
    SDKTaskStatus, SDKDeploymentStatus, SDKFileChange
)

# Configure a null handler for the library's root logger
# This prevents a "No handler found" warning if the consuming application
# doesn't configure logging, while still allowing the app to set up its own handlers.
logging.getLogger(__name__).addHandler(logging.NullHandler())


__all__ = [
    # ... existing exports ...
    "BuildSystemClient" # Add this
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
    "SDKFileChange"

# This makes 'sdk' a sub-package of 'interfaces'.
# It can export specific client utilities or interfaces if needed.
from .debugiq_sdk import DebugIQClient
from .codenav_sdk import CodeNavSDKClient
# from .agent_client import GenericAgentClient (if we define it)

__all__ = ["DebugIQClient", "CodeNavSDKClient"]
from .models import SDKMCPStrategyRequestContext, SDKMCPStrategyResponse # <<< ADD THESE

__all__ = [
    # ... (existing exports) ...
    "SDKMCPStrategyRequestContext", "SDKMCPStrategyResponse", # <<< ADD THESE
]
