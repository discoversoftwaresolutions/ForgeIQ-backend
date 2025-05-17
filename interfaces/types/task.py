# ================================
# 📁 interfaces/types/task.py
# ================================
from typing import TypedDict, Optional, Dict, Any, List
from .common import Status, Timestamped # Import from common.py

class TaskInfo(Timestamped): # More generic task info
    task_id: str
    task_type: str
    project_id: Optional[str]
    status: Status
    details: Optional[Dict[str, Any]]

class TaskExecutionRequest(TypedDict): # If an agent explicitly requests task execution
    request_id: str
    task_type: str
    project_id: Optional[str]
    commit_sha: Optional[str]
    parameters: Optional[Dict[str, Any]]
    context: Optional[Dict[str, Any]]

class TaskExecutionResult(TypedDict): # Generic result structure
    task_id: str # Or corresponds to request_id
    status: Status
    message: Optional[str]
    output_summary: Optional[str]
    artifacts_produced: Optional[List[Dict[str, str]]] # e.g., [{"name": "log.txt", "location": "s3://..."}]
    started_at: str
    completed_at: str

# TaskStatus was previously in events.py, fits well here too.
# This is what PlanAgent uses to report status of individual DAG nodes.
class TaskStatus(TypedDict):
    task_id: str # Corresponds to DagNode id
    status: Status 
    message: Optional[str]
    result_summary: Optional[str] 
    started_at: Optional[str] 
    completed_at: Optional[str]
