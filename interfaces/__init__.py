# ==================================
# 📁 interfaces/types/__init__.py
# ==================================

# Corrected line - removed invalid comment syntax
from .events import (
    TestResult, TestFailedEvent,
    CodeNavSearchQuery, CodeNavSearchResultItem, CodeNavSearchResults,
    PatchSuggestion, PatchSuggestedEvent,
    # Add other specific events defined in ./events.py that you want to export from interfaces.types
    PipelineGenerationUserPrompt, PipelineGenerationRequestEvent, DagDefinitionCreatedEvent,
    TaskStatus, TaskStatusUpdateEvent, DagExecutionStatusEvent,
    NewCommitEvent, FileChange, AffectedTasksIdentifiedEvent,
    NewArtifactEvent, SecurityFinding, SecurityScanResultEvent,
    AuditLogEntry, SLAMetric, SLAViolationEvent, GovernanceAlertEvent,
    DeploymentRequestEvent, DeploymentStatusEvent,InterfacesEvents
)

from .common import Status, Timestamped # Assuming common.py for these
from .agent import AgentCapability, AgentEndpoint, AgentRegistrationInfo
from .cache import CacheKeyParams, CachedItemMetadata, CacheGetResponse, CacheStoreRequest, CacheStoreResponse
from .graph import DagNode, DagDefinition # Moving these here
from .task import TaskInfo, TaskExecutionRequest, TaskExecutionResult # New task types


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
    "TaskInfo", "TaskExecutionRequest", "TaskExecutionResult", # New
    # Events (alphabetical for easier management) - Ensure these are imported above
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
