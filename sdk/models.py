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
# In sdk/models.py (and corresponding Pydantic model in apps/forgeiq-backend/app/api_models.py)

class SDKAlgorithmContext(TypedDict): # Or Pydantic BaseModel if SDK uses Pydantic
    project_id: str # Matches existing naming conventions
    dag_representation: List[Any] # Could be list of task names, or more structured if needed
    telemetry_data: Dict[str, Any]
    # Could add user_prompt_for_optimization: Optional[str] if needed

class SDKOptimizedAlgorithmResponse(TypedDict): # Or Pydantic
    algorithm_reference: str
    benchmark_score: float
    generated_code_or_dag: str # Or a more structured DAG type
    message: Optional[str]
# In apps/forgeiq-backend/app/api_models.py
class OptimizeStrategyRequest(BaseModel):
    dag_representation: List[Any]
    telemetry_data: Dict[str, Any]

class OptimizedAlgorithmDetails(BaseModel):
    algorithm_reference: str
    benchmark_score: float
    generated_code_or_dag: str # This could be a string or a more structured DAG model

class OptimizeStrategyResponse(BaseModel):
    message: str
    optimization_details: Optional[OptimizedAlgorithmDetails] = None
    status: str = "processing_initiated" # Or "completed" if synchronous
