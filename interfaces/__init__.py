# ... (existing exports) ...
from .events import (
    DeploymentRequestEvent, DeploymentStatusEvent
)

__all__ = [
    # ... (existing exports) ...
    "DeploymentRequestEvent", "DeploymentStatusEvent",
]
from .events import (
    TestResult, TestFailedEvent,
    CodeNavSearchQuery, CodeNavSearchResultItem, CodeNavSearchResults,
    PatchSuggestion, PatchSuggestedEvent
)
# ==================================
# 📁 interfaces/types/__init__.py
# ==================================
from .common import Status, Timestamped # Assuming common.py for these
from .agent import AgentCapability, AgentEndpoint, AgentRegistrationInfo
from .cache import CacheKeyParams, CachedItemMetadata, CacheGetResponse, CacheStoreRequest, CacheStoreResponse
from .graph import DagNode, DagDefinition # Moving these here
from .task import TaskInfo, TaskExecutionRequest, TaskExecutionResult # New task types
from .events import (
    # Core Operational Events
    TestResult, TestFailedEvent,
    PipelineGenerationUserPrompt, PipelineGenerationRequestEvent, DagDefinitionCreatedEvent,
    TaskStatus, TaskStatusUpdateEvent, DagExecutionStatusEvent,
    NewCommitEvent, FileChange, AffectedTasksIdentifiedEvent,
    NewArtifactEvent, SecurityFinding, SecurityScanResultEvent,
    DeploymentRequestEvent, DeploymentStatusEvent,
    # Governance Events
    AuditLogEntry, SLAMetric, SLAViolationEvent, GovernanceAlertEvent,
    # CodeNav Events (if any were defined, or use specific SDK models for CodeNav)
    CodeNavSearchQuery, CodeNavSearchResultItem, CodeNavSearchResults, # From previous definitions
    # PatchAgent Events
    PatchSuggestion, PatchSuggestedEvent
)

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
    # Events (alphabetical for easier management)
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
