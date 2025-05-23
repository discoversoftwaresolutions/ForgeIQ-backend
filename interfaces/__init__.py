# ===================================
# 📁 /app/interfaces/__init__.py
# ===================================

# CORRECTED IMPORT PATH: Referencing the 'events' module inside the 'types' submodule
# The 'events' module is expected to be in interfaces/types/events.py
from .types.events import (
    TestResult, TestFailedEvent,
    CodeNavSearchQuery, CodeNavSearchResultItem, CodeNavSearchResults,
    PatchSuggestion, PatchSuggestedEvent,
    DeploymentStatusEvent, # Add other specific events needed at this level
    DeploymentRequestEvent,
    PipelineGenerationUserPrompt, PipelineGenerationRequestEvent,
    DagDefinitionCreatedEvent,
    TaskStatus, TaskStatusUpdateEvent, DagExecutionStatusEvent,
    NewCommitEvent, FileChange, AffectedTasksIdentifiedEvent,
    NewArtifactEvent, SecurityFinding, SecurityScanResultEvent,
    AuditLogEntry, SLAMetric, SLAViolationEvent, GovernanceAlertEvent,
)

# You may also want to expose other types defined within the 'types' submodule
from .types.common import Status, Timestamped
from .types.agent import AgentCapability, AgentEndpoint, AgentRegistrationInfo
from .types.cache import (
    CacheKeyParams, CachedItemMetadata, CacheGetResponse,
    CacheStoreRequest, CacheStoreResponse
)
from .types.graph import DagNode, DagDefinition
from .types.task import TaskInfo, TaskExecutionRequest, TaskExecutionResult


__all__ = [
    # Common
    "Status", "Timestamped",
    # Agent
    "AgentCapability", "AgentEndpoint", "AgentRegistrationInfo",
    # Cache
    "CacheKeyParams", "CachedItemMetadata", "CacheGetResponse",
    "CacheStoreRequest", "CacheStoreResponse",
    # Graph
    "DagNode", "DagDefinition",
    # Task
    "TaskInfo", "TaskExecutionRequest", "TaskExecutionResult",

    # Re-exporting specific events from interfaces.types.events
    "AffectedTasksIdentifiedEvent",
    "AuditLogEntry",
    "CodeNavSearchQuery", "CodeNavSearchResultItem", "CodeNavSearchResults",
    "DagDefinitionCreatedEvent", "DagExecutionStatusEvent",
    "DeploymentRequestEvent", "DeploymentStatusEvent",
    "FileChange",
    "GovernanceAlertEvent",
    "NewArtifactEvent", "NewCommitEvent",
    "PatchSuggestion", "PatchSuggestedEvent",
    "PipelineGenerationUserPrompt", "PipelineGenerationRequestEvent",
    "SLAMetric", "SLAViolationEvent",
    "SecurityFinding", "SecurityScanResultEvent",
    "TaskStatus", "TaskStatusUpdateEvent",
    "TestFailedEvent", "TestResult",
]

# Note: The __all__ list should include all names you want to make available
# when someone does 'from interfaces import *'.
# Ensure all names imported above that you want to export are in __all__.
