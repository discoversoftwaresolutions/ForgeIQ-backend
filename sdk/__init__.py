
# =======================
# 📁 sdk/__init__.py
# =======================
import logging # Good practice to configure a null handler for library logs by default

from .client import ForgeIQClient
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
