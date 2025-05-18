# ===================================
# 📁 /app/interfaces/__init__.py
# ===================================

# Assuming event definitions are in interfaces/types/events.py
# Correct the import path to reference the 'types' submodule
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
    # Re-exporting types from the 'types' submodule
    "Status", "Timestamped",
    "AgentCapability", "AgentEndpoint", "AgentRegistrationInfo",
    "CacheKeyParams", "CachedItemMetadata", "CacheGetResponse",
    "CacheStoreRequest", "CacheStoreResponse",
    "DagNode", "DagDefinition",
    "TaskInfo", "TaskExecutionRequest", "TaskExecutionResult",

    # Re-exporting specific events from interfaces.types.events
    "TestResult", "TestFailedEvent",
    "CodeNavSearchQuery", "CodeNavSearchResultItem", "CodeNavSearchResults",
    "PatchSuggestion", "PatchSuggestedEvent",
    "DeploymentStatusEvent",
    "DeploymentRequestEvent",
    "PipelineGenerationUserPrompt", "PipelineGenerationRequestEvent",
    "DagDefinitionCreatedEvent",
    "TaskStatus", "TaskStatusUpdateEvent", "DagExecutionStatusEvent",
    "NewCommitEvent", "FileChange", "AffectedTasksIdentifiedEvent",
    "NewArtifactEvent", "SecurityFinding", "SecurityScanResultEvent",
    "AuditLogEntry", "SLAMetric", "SLAViolationEvent", "GovernanceAlertEvent",
]

# Note: The __all__ list should include all names you want to make available
# when someone does 'from interfaces import *'.
# Ensure all names imported above that you want to export are in __all__.
