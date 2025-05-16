# ======================
# 📁 sdk/models.py
# ======================
from typing import TypedDict, List, Dict, Any, Optional

class SDKFileChange(TypedDict):
    file_path: str
    change_type: str 
    old_file_path: Optional[str]

class SDKDagNode(TypedDict):
    id: str
    task_type: str
    command: Optional[List[str]]
    agent_handler: Optional[str]
    params: Optional[Dict[str, Any]]
    dependencies: List[str]

class SDKDagDefinition(TypedDict):
    dag_id: str
    project_id: Optional[str]
    nodes: List[SDKDagNode]

class SDKTaskStatus(TypedDict):
    task_id: str
    status: str
    message: Optional[str]
    result_summary: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]

class SDKDagExecutionStatus(TypedDict):
    dag_id: str
    project_id: Optional[str]
    status: str
    message: Optional[str]
    started_at: str
    completed_at: Optional[str]
    task_statuses: List[SDKTaskStatus]

class SDKDeploymentStatus(TypedDict):
    deployment_id: str
    request_id: str
    project_id: str
    service_name: str
    commit_sha: str
    target_environment: str
    status: str
    message: Optional[str]
    deployment_url: Optional[str]
    logs_url: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
