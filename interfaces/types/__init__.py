# ==================================
# 📁 /app/interfaces/types/__init__.py
# ==================================

# Corrected import statements - removed invalid /* */ comment syntax

# Imports from events.py
from .events import (
    TestResult, TestFailedEvent,
    CodeNavSearchQuery, CodeNavSearchResultItem, CodeNavSearchResults,
    PatchSuggestion, PatchSuggestedEvent,
    PipelineGenerationRequest, # Assuming PipelineGenerationRequest is here or moved
    DagNode, DagDefinition, # Assuming DagNode, DagDefinition are here or moved
    DagDefinitionCreatedEvent,
    TaskStatus, TaskStatusUpdateEvent, DagExecutionStatusEvent,
    ProprietaryAuditEvent, # Added ProprietaryAuditEvent
    # Add other specific event types defined in ./events.py
    NewCommitEvent, FileChange, AffectedTasksIdentifiedEvent, # From previous /interfaces/__init__.py content
    NewArtifactEvent, SecurityFinding, SecurityScanResultEvent, # From previous /interfaces/__init__.py content
    AuditLogEntry, SLAMetric, SLAViolationEvent, GovernanceAlertEvent, # From previous /interfaces/__init__.py content
    DeploymentRequestEvent, DeploymentStatusEvent, # From previous /interfaces/__init__.py content
    PipelineGenerationUserPrompt, PipelineGenerationRequestEvent, # From previous /interfaces/__init__.py content
)

# Imports from cache.py
from .cache import (
    CacheKeyParams, CachedItemMetadata,
    CacheGetResponse, CacheStoreRequest, CacheStoreResponse
)

# Imports from agent.py
from .agent import (
    AgentCapability, AgentEndpoint, AgentRegistrationInfo
)

# Imports from orchestration.py
from .orchestration import OrchestrationFlowState

# Imports from common.py (based on previous interaction)
from .common import Status, Timestamped

# Imports from graph.py (based on previous interaction, although DagNode/DagDefinition also in events import list)
# Clarify where DagNode/DagDefinition truly reside. Assuming graph.py for now based on previous __init__.py
from .graph import DagNode, DagDefinition

# Imports from task.py (based on previous interaction)
from .task import TaskInfo, TaskExecutionRequest, TaskExecutionResult


__all__ = [
    # Common types
    "Status", "Timestamped",

    # Agent types
    "AgentCapability", "AgentEndpoint", "AgentRegistrationInfo",

    # Cache types
    "CacheKeyParams", "CachedItemMetadata",
    "CacheGetResponse", "CacheStoreRequest", "CacheStoreResponse",

    # Graph types
    # Including here based on graph.py import, even if also listed in events import
    "DagNode", "DagDefinition",

    # Task types
    "TaskInfo", "TaskExecutionRequest", "TaskExecutionResult",

    # Orchestration types
    "OrchestrationFlowState",

    # Event types (alphabetical for easier management, ensure these are imported above)
    "AffectedTasksIdentifiedEvent",
    "AuditLogEntry",
    "CodeNavSearchQuery", "CodeNavSearchResultItem", "CodeNavSearchResults",
    "DagDefinitionCreatedEvent", "DagExecutionStatusEvent",
    "DeploymentRequestEvent", "DeploymentStatusEvent",
    "FileChange",
    "GovernanceAlertEvent",
    "NewArtifactEvent", "NewCommitEvent",
    "PatchSuggestion", "PatchSuggestedEvent",
    "PipelineGenerationRequest", "PipelineGenerationUserPrompt", "PipelineGenerationRequestEvent",
    "ProprietaryAuditEvent",
    "SLAMetric", "SLAViolationEvent",
    "SecurityFinding", "SecurityScanResultEvent",
    "TaskStatus", "TaskStatusUpdateEvent",
    "TestFailedEvent", "TestResult",
    # Add any other specific event types defined in ./events.py that you need to re-export
]

# Note: Ensure that all names listed in __all__ are actually imported in this file
# from one of the modules within the 'types' package (e.g., events.py, cache.py, etc.).
# If an item is listed in __all__ but not imported, it will cause a NameError.
