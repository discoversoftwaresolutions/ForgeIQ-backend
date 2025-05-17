# interfaces/types/__init__.py
from .events import ( /* existing event imports */ )
from .cache import ( /* existing cache imports */ )
from .agent import ( # <<< NEW
    AgentCapability, AgentEndpoint, AgentRegistrationInfo
)

__all__ = [
    # ... (existing exports from events.py and cache.py) ...
    "AgentCapability", "AgentEndpoint", "AgentRegistrationInfo", # <<< NEW
]# interfaces/types/__init__.py
from .events import (
    TestResult, TestFailedEvent,
    CodeNavSearchQuery, CodeNavSearchResultItem, CodeNavSearchResults,
    PatchSuggestion, PatchSuggestedEvent,
    PipelineGenerationRequest, DagNode, DagDefinition, DagDefinitionCreatedEvent,
    TaskStatus, TaskStatusUpdateEvent, DagExecutionStatusEvent
)
from .cache import ( # <<< NEW
    CacheKeyParams, CachedItemMetadata, 
    CacheGetResponse, CacheStoreRequest, CacheStoreResponse
)

__all__ = [
    "TestResult", "TestFailedEvent",
    "CodeNavSearchQuery", "CodeNavSearchResultItem", "CodeNavSearchResults",
    "PatchSuggestion", "PatchSuggestedEvent",
    "PipelineGenerationRequest", "DagNode", "DagDefinition", "DagDefinitionCreatedEvent",
    "TaskStatus", "TaskStatusUpdateEvent", "DagExecutionStatusEvent",
    "CacheKeyParams", "CachedItemMetadata", # <<< NEW
    "CacheGetResponse", "CacheStoreRequest", "CacheStoreResponse" # <<< NEW
]
# interfaces/types/__init__.py
# ... (all existing imports and __all__ entries) ...
from .orchestration import OrchestrationFlowState

__all__.extend([
    "OrchestrationFlowState",
])# ... (existing exports) ...
from .events import ProprietaryAuditEvent

__all__.extend([
    # ... (existing exports) ...
    "ProprietaryAuditEvent",
])
