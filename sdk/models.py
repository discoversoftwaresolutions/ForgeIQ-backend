# =======================
# 📁 sdk/models.py
# =======================
from typing import TypedDict, List, Dict, Any, Optional
from pydantic import BaseModel, Field  # ✅ Fix: Added missing Field import

# --- File Change Representation ---
class SDKFileChange(TypedDict):
    file_path: str
    change_type: str 
    old_file_path: Optional[str]

# --- DAG Node Representation ---
class SDKDagNode(TypedDict):
    id: str
    task_type: str
    command: Optional[List[str]]
    agent_handler: Optional[str]
    params: Optional[Dict[str, Any]]
    dependencies: List[str]

# --- DAG Definition ---
class SDKDagDefinition(TypedDict):
    dag_id: str
    project_id: Optional[str]
    nodes: List[SDKDagNode]

# --- Task Status Representation ---
class SDKTaskStatus(TypedDict):
    task_id: str
    status: str
    message: Optional[str]
    result_summary: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]

# --- DAG Execution Status ---
class SDKDagExecutionStatus(TypedDict):
    dag_id: str
    project_id: Optional[str]
    status: str
    message: Optional[str]
    started_at: str
    completed_at: Optional[str]
    task_statuses: List[SDKTaskStatus]

# --- Deployment Status ---
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

# --- Optimization Strategy Representation ---
class SDKAlgorithmContext(TypedDict):
    project_id: str
    dag_representation: List[Any]
    telemetry_data: Dict[str, Any]

class SDKOptimizedAlgorithmResponse(TypedDict):
    algorithm_reference: str
    benchmark_score: float
    generated_code_or_dag: Optional[str]  # ✅ Fix: Added Optional
    message: Optional[str]

# --- MCP Strategy Request ---
class SDKMCPStrategyRequestContext(TypedDict):
    project_id: str
    current_dag_snapshot: Optional[List[Dict[str, Any]]]
    optimization_goal: Optional[str]
    additional_mcp_context: Optional[Dict[str, Any]]

class SDKMCPStrategyResponse(TypedDict):
    project_id: str
    strategy_id: Optional[str]
    new_dag_definition: Optional[SDKDagDefinition]
    directives: Optional[List[str]]
    status: str
    message: Optional[str]
    mcp_execution_details: Optional[Dict[str, Any]]

# --- Pydantic API Models ---
class OptimizeStrategyRequest(BaseModel):
    dag_representation: List[Any]
    telemetry_data: Dict[str, Any]

class OptimizedAlgorithmDetails(BaseModel):
    algorithm_reference: str
    benchmark_score: float
    generated_code_or_dag: Optional[str]  # ✅ Fix: Added Optional

class OptimizeStrategyResponse(BaseModel):
    message: str
    optimization_details: Optional[OptimizedAlgorithmDetails] = None
    status: str = "processing_initiated"

# --- MCP API Models ---
class MCPStrategyApiRequest(BaseModel):
    current_dag_snapshot: Optional[List[Dict[str, Any]]] = Field(default=None, description="Snapshot of the current DAG.")
    optimization_goal: Optional[str] = Field(default=None, description="Optimization goal (e.g., 'reduce_cost').")
    additional_mcp_context: Optional[Dict[str, Any]] = Field(default=None, description="Additional context.")

class MCPStrategyApiDetails(BaseModel):
    strategy_id: Optional[str] = None
    new_dag_definition_raw: Optional[Dict[str, Any]] = Field(default=None, description="Raw new DAG definition.")
    directives: Optional[List[str]] = None
    mcp_metadata: Optional[Dict[str, Any]] = Field(default=None, alias="mcpExecutionDetails")

class MCPStrategyApiResponse(BaseModel):
    project_id: str
    status: str
    message: Optional[str] = None
    strategy_details: Optional[MCPStrategyApiDetails] = None
