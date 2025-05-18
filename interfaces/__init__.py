# ==================================
# 📁 interfaces/types/__init__.py
# ==================================

# Corrected import statement - removed the invalid comment syntax
# from .events import 
from .events import (
    TestResult, TestFailedEvent,
    CodeNavSearchQuery, CodeNavSearchResultItem, CodeNavSearchResults,
    PatchSuggestion, PatchSuggestedEvent,
    # Core Operational Events
    PipelineGenerationUserPrompt, PipelineGenerationRequestEvent, DagDefinitionCreatedEvent,
    TaskStatus, TaskStatusUpdateEvent, DagExecutionStatusEvent,
    NewCommitEvent, FileChange, AffectedTasksIdentifiedEvent,
    NewArtifactEvent, SecurityFinding, SecurityScanResultEvent,
    DeploymentRequestEvent, DeploymentStatusEvent,
    # Governance Events
    AuditLogEntry, SLAMetric, SLAViolationEvent, GovernanceAlertEvent,
    # Add any other specific event types defined in ./events.py that you need to re-export
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
