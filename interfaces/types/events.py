# ==========================================
# 📁 interfaces/types/events.py
# ==========================================
from typing import TypedDict, List, Optional, Dict, Any

class TestResult(TypedDict):
    test_name: str
    status: str  # 'passed', 'failed', 'skipped'
    message: Optional[str]
    stack_trace: Optional[str]

class TestFailedEvent(TypedDict):
    event_type: str  # "TestFailedEvent"
    project_id: str
    commit_sha: str
    failed_tests: List[TestResult]
    full_log_path: Optional[str]
    timestamp: str

class CodeNavSearchQuery(TypedDict):
    query_type: str  # "CodeNavSearchQuery"
    query_text: str
    project_id: str
    limit: Optional[int]
    filters: Optional[Dict[str, Any]]  # e.g., {"file_extension": ".py"}

class CodeNavSearchResultItem(TypedDict):
    file_path: str
    snippet: str  # or code_chunk
    score: float
    metadata: Optional[Dict[str, Any]]

class CodeNavSearchResults(TypedDict):
    event_type: str  # "CodeNavSearchResults"
    query_id: str  # Correlate with query
    results: List[CodeNavSearchResultItem]

class PatchSuggestion(TypedDict):
    file_path: str
    start_line: int
    end_line: int
    suggested_code: str
    confidence: Optional[float]
    reasoning: Optional[str]

class PatchSuggestedEvent(TypedDict):
    event_type: str  # "PatchSuggestedEvent"
    project_id: str
    commit_sha: str
    related_test_failure_id: Optional[str]
    suggestions: List[PatchSuggestion]
    timestamp: str

class PipelineGenerationUserPrompt(TypedDict):
    event_type: str  # "PipelineGenerationUserPrompt"
    request_id: str  # Unique request ID
    user_prompt: str  # Natural language prompt describing pipeline needs
    project_id: Optional[str]
    context: Optional[Dict[str, Any]]  # Any additional parameters for the request
    timestamp: str

# ✅ New event: PipelineGenerationRequestEvent
class PipelineGenerationRequestEvent(TypedDict):
    event_type: str  # "PipelineGenerationRequestEvent"
    request_id: str
    project_id: Optional[str]
    prompt_details: Dict[str, Any]
    timestamp: str

# ✅ New event: DagExecutionStatusEvent
class DagExecutionStatusEvent(TypedDict):
    event_type: str  # "DagExecutionStatusEvent"
    dag_id: str
    project_id: str
    status: str  # e.g., "running", "completed", "failed"
    details: Optional[Dict[str, Any]]
    timestamp: str

# ✅ New event: AffectedTasksIdentifiedEvent
class AffectedTasksIdentifiedEvent(TypedDict):
    event_type: str  # "AffectedTasksIdentifiedEvent"
    project_id: str
    affected_tasks: List[str]
    timestamp: str

# ✅ New event: FileChange
class FileChange(TypedDict):
    event_type: str  # "FileChange"
    file_path: str
    change_type: str  # e.g., "modified", "added", "deleted"
    commit_sha: str
    timestamp: str

# ✅ New event: NewCommitEvent
class NewCommitEvent(TypedDict):
    event_type: str  # "NewCommitEvent"
    commit_sha: str
    project_id: str
    author: Optional[str]
    message: Optional[str]
    timestamp: str

# ✅ New event: TaskStatus & TaskStatusUpdateEvent
class TaskStatus(TypedDict):
    task_id: str
    status: str  # "queued", "in_progress", "completed", "failed"
    timestamp: str

class TaskStatusUpdateEvent(TypedDict):
    event_type: str  # "TaskStatusUpdateEvent"
    project_id: str
    updated_tasks: List[TaskStatus]

# ✅ Existing event definitions retained
class DeploymentStatusEvent(TypedDict):
    event_type: str  # "DeploymentStatusEvent"
    deployment_id: str  # Unique ID for the deployment
    project_id: str
    commit_sha: Optional[str]
    status: str  # e.g., 'PENDING', 'IN_PROGRESS', 'SUCCESS', 'FAILED', 'CANCELLED'
    message: Optional[str]  # Optional status message
    details: Optional[Dict[str, Any]]  # Optional deployment details
    timestamp: str

class GovernanceAlertEvent(TypedDict):
    event_type: str  # "GovernanceAlertEvent"
    alert_id: str
    severity: str  # 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'
    description: str
    details: Optional[Dict[str, Any]]

class SLAMetric(TypedDict):
    metric_name: str
    value: float
    unit: str  # e.g., "seconds", "percentage"
    project_id: Optional[str]
    dag_id: Optional[str]
    task_id: Optional[str]

class SLAViolationEvent(TypedDict):
    event_type: str  # "SLAViolationEvent"
    alert_id: str
    timestamp: str
    observed_value: float
    threshold_value: float
    project_id: Optional[str]
    details: str

# Ensure all relevant event types are included in __all__
__all__ = [
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
    "TestFailedEvent", "TestResult"
]
