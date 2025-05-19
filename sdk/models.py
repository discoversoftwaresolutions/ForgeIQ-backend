
from typing import TypedDict, List, Dict, Any, Optional
from pydantic import BaseModel  
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
# =================================================================
# 📁 sdk/models.py (additions for MCP interaction)
# =================================================================
# ... (existing SDK TypedDicts like SDKDagDefinition, etc.) ...

class SDKMCPStrategyRequestContext(TypedDict):
    """Context sent to request an MCP strategy."""
    project_id: str
    # current_dag_snapshot could be List[SDKDagNode], List[str] (task names), or more complex
    current_dag_snapshot: Optional[List[Dict[str, Any]]] 
    optimization_goal: Optional[str] # e.g., "reduce_build_time", "enhance_security_coverage"
    additional_mcp_context: Optional[Dict[str, Any]]

class SDKMCPStrategyResponse(TypedDict):
    """Response from the MCP after a strategy request."""
    project_id: str
    strategy_id: Optional[str]
    # new_dag_definition might be a full SDKDagDefinition or a set of modifications
    new_dag_definition: Optional[SDKDagDefinition] # Or Dict[str, Any] if structure varies
    directives: Optional[List[str]] # High-level instructions
    status: str # e.g., "strategy_provided", "no_optimization_needed", "error"
    message: Optional[str]
    mcp_execution_details: Optional[Dict[str, Any]] # For any metadata from MCP
# =======================================================================
# 📁 apps/forgeiq-backend/app/api_models.py (additions for MCP interaction)
# =======================================================================
# ... (existing Pydantic models like PipelineGenerateRequest, etc.) ...

class MCPStrategyApiRequest(BaseModel):
    # This is what the backend API endpoint will expect in its request body
    # It should correspond to what the SDK's SDKMCPStrategyRequestContext provides
    current_dag_snapshot: Optional[List[Dict[str, Any]]] = Field(default=None, description="Snapshot of the current DAG, e.g., list of nodes or simplified structure.")
    optimization_goal: Optional[str] = Field(default=None, description="Specific goal for the MCP strategy, e.g., 'reduce_cost'.")
    additional_mcp_context: Optional[Dict[str, Any]] = Field(default=None, description="Any other context for the MCP.")

class MCPStrategyApiDetails(BaseModel): # Part of the response
    strategy_id: Optional[str] = None
    # new_dag_definition: Optional[SDKDagDefinitionModel] = None # If returning a full DAG
    new_dag_definition_raw: Optional[Dict[str, Any]] = Field(default=None, description="Raw new DAG definition or modifications from MCP.")
    directives: Optional[List[str]] = None
    mcp_metadata: Optional[Dict[str, Any]] = Field(default=None, alias="mcpExecutionDetails")


class MCPStrategyApiResponse(BaseModel):
    project_id: str
    status: str # e.g., "STRATEGY_SUGGESTED", "NO_ACTION_NEEDED", "MCP_ERROR"
    message: Optional[str] = None
    strategy_details: Optional[MCPStrategyApiDetails] = None
