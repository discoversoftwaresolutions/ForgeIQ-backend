# ==========================================
# 📁 interfaces/types/events.py
# ==========================================
from typing import TypedDict, List, Optional, Dict, Any

# --- Task & Code Events ---
class AffectedTasksIdentifiedEvent(TypedDict):
    event_type: str  # "AffectedTasksIdentifiedEvent"
    project_id: str
    affected_tasks: List[str]
    timestamp: str

class TestResult(TypedDict):
    test_name: str
    status: str  # "passed", "failed", "skipped"
    message: Optional[str]
    stack_trace: Optional[str]

class TestFailedEvent(TypedDict):
    event_type: str  # "TestFailedEvent"
    project_id: str
    commit_sha: str
    failed_tests: List[TestResult]
    full_log_path: Optional[str]
    timestamp: str

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

class FileChange(TypedDict):
    event_type: str  # "FileChange"
    file_path: str
    change_type: str  # "modified", "added", "deleted"
    commit_sha: str
    timestamp: str

class NewCommitEvent(TypedDict):
    event_type: str  # "NewCommitEvent"
    commit_sha: str
    project_id: str
    author: Optional[str]
    message: Optional[str]
    timestamp: str

class TaskStatus(TypedDict):
    task_id: str
    status: str  # "queued", "in_progress", "completed", "failed"
    timestamp: str

class TaskStatusUpdateEvent(TypedDict):
    event_type: str  # "TaskStatusUpdateEvent"
    project_id: str
    updated_tasks: List[TaskStatus]

# --- Deployment Events ---
class DeploymentRequestEvent(TypedDict):
    event_type: str  # "DeploymentRequestEvent"
    request_id: str
    project_id: str
    deployment_target: Optional[str]
    parameters: Optional[Dict[str, Any]]
    timestamp: str

class DeploymentStatusEvent(TypedDict):
    event_type: str  # "DeploymentStatusEvent"
    deployment_id: str
    project_id: str
    commit_sha: Optional[str]
    status: str  # "PENDING", "IN_PROGRESS", "SUCCESS", "FAILED", "CANCELLED"
    message: Optional[str]
    details: Optional[Dict[str, Any]]
    timestamp: str

# --- Pipeline Events ---
class PipelineGenerationRequest(TypedDict):
    event_type: str  # "PipelineGenerationRequest"
    request_id: str
    user_prompt: str
    project_id: Optional[str]
    context: Optional[Dict[str, Any]]
    timestamp: str

class PipelineGenerationUserPrompt(TypedDict):
    event_type: str  # "PipelineGenerationUserPrompt"
    request_id: str
    project_id: Optional[str]
    prompt_details: Dict[str, Any]
    timestamp: str

class PipelineGenerationRequestEvent(TypedDict):
    event_type: str  # "PipelineGenerationRequestEvent"
    request_id: str
    project_id: Optional[str]
    context: Dict[str, Any]
    timestamp: str

# --- DAG Execution Events ---
class DagExecutionStatusEvent(TypedDict):
    event_type: str  # "DagExecutionStatusEvent"
    dag_id: str
    project_id: str
    status: str  # "running", "completed", "failed"
    details: Optional[Dict[str, Any]]
    timestamp: str

class DagDefinitionCreatedEvent(TypedDict):
    event_type: str  # "DagDefinitionCreatedEvent"
    request_id: str
    project_id: Optional[str]
    dag: Dict[str, Any]  # Adjust this to match your DAG structure
    timestamp: str

# --- Artifact Events ---
class NewArtifactEvent(TypedDict):
    event_type: str  # "NewArtifactEvent"
    event_id: str
    project_id: str
    commit_sha: Optional[str]
    artifact_name: str
    artifact_type: str  # "docker_image", "python_wheel", "terraform_plan"
    artifact_location: str  # "registry/image:tag", "s3://bucket/path"
    timestamp: str

# --- Security & Governance Events ---
class SecurityFinding(TypedDict):
    finding_id: str
    rule_id: Optional[str]
    severity: str  # "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL"
    description: str
    file_path: Optional[str]
    line_number: Optional[int]
    code_snippet: Optional[str]
    remediation: Optional[str]
    tool_name: str

class SecurityScanResultEvent(TypedDict):
    event_type: str  # "SecurityScanResultEvent"
    triggering_event_id: str
    project_id: str
    commit_sha: Optional[str]
    artifact_name: Optional[str]
    scan_type: str  # "SAST", "SCA_PYTHON", "IAC_TERRAFORM"
    tool_name: str
    status: str  # "SUCCESS", "FAILED_TO_SCAN", "COMPLETED_WITH_FINDINGS"
    findings: List[SecurityFinding]
    summary: Optional[str]
    timestamp: str

class GovernanceAlertEvent(TypedDict):
    event_type: str  # "GovernanceAlertEvent"
    alert_id: str
    severity: str  # "CRITICAL", "HIGH", "MEDIUM", "LOW"
    description: str
    details: Optional[Dict[str, Any]]

class SLAMetric(TypedDict):
    metric_name: str
    value: float
    unit: str  # "seconds", "percentage"
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

# --- Audit Events ---
class AuditLogEntry(TypedDict):
    event_type: str  # "AuditEvent"
    audit_id: str
    timestamp: str
    source_event_type: str
    source_event_id: Optional[str]
    service_name: Optional[str]
    project_id: Optional[str]
    commit_sha: Optional[str]
    user_or_actor: Optional[str]
    action_taken: str
    details: Dict[str, Any]

class ProprietaryAuditEvent(TypedDict):
    event_type: str  # "ProprietaryAuditEvent"
    audit_id: str
    timestamp: str
    source_service: str
    actor: Optional[str]
    action: str
    data_payload: Dict[str, Any]

class CodeNavSearchQuery(TypedDict):
    query_type: str  # "CodeNavSearchQuery"
    query_text: str
    project_id: str
    limit: Optional[int]
    filters: Optional[Dict[str, Any]]  # e.g., {"file_extension": ".py"}

# Ensure all event types are included in __all__
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
