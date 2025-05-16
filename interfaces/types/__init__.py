
# interfaces/types/__init__.py
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
