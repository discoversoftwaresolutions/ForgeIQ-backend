from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
import uuid  # ✅ Used for default request ID generation

# --- Code Generation Request Model ---

class CodeGenerationRequest(BaseModel):
    """Defines request structure for code generation."""
    project_id: str
    prompt_text: str
    config_options: Dict[str, Any]  # ✅ Changed from `dict` for type safety

# --- Request Models ---

class UserPromptData(BaseModel):
    """Defines user input structure for pipeline generation."""
    prompt_text: str
    target_project_id: Optional[str] = None
    additional_context: Optional[Dict[str, Any]] = None

class PipelineGenerateRequest(BaseModel):
    """Model for pipeline generation requests."""
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    user_prompt_data: UserPromptData

class DeploymentTriggerRequest(BaseModel):
    """Model for triggering deployments."""
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    service_name: str
    commit_sha: str
    target_environment: str
    triggered_by: Optional[str] = "API"

# --- Response Models ---

class PipelineGenerateResponse(BaseModel):
    """Response model for pipeline generation."""
    message: str
    request_id: str
    status: str = "accepted"

class DeploymentTriggerResponse(BaseModel):
    """Response model for deployment triggers."""
    message: str
    request_id: str
    status: str = "accepted"

class SDKTaskStatusModel(BaseModel):
    """Defines status information for SDK tasks."""
    task_id: str
    status: str
    message: Optional[str] = None
    result_summary: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

class SDKDagExecutionStatusModel(BaseModel):
    """Defines execution status for DAGs."""
    dag_id: str
    project_id: Optional[str] = None
    status: str
    message: Optional[str] = None
    started_at: Optional[str] = None  # ✅ Optional for NOT_FOUND cases
    completed_at: Optional[str] = None
    task_statuses: List[SDKTaskStatusModel] = []

class SDKDeploymentStatusModel(BaseModel):
    """Defines deployment status for SDK services."""
    deployment_id: Optional[str] = None  # ✅ Might not be known if status is PENDING_TRIGGER
    request_id: str
    project_id: str
    service_name: str
    commit_sha: Optional[str] = None
    target_environment: Optional[str] = None
    status: str
    message: Optional[str] = None
    deployment_url: Optional[str] = None
    logs_url: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

class ProjectConfigResponse(BaseModel):
    """Defines project configuration response."""
    project_id: str
    configuration: Dict[str, Any]  # ✅ Or define a specific model for configuration structure

class BuildGraphNodeModel(BaseModel):
    """Represents a single node in a build graph."""
    id: str
    task_type: str  # ✅ Or 'name'
    dependencies: List[str]

class BuildGraphResponse(BaseModel):
    """Defines the DAG response structure."""
    project_id: str
    tasks_sequence: List[str]  # ✅ Based on `core.build_graph.get_project_dag` output
    # If DAGs returned more detailed node structures, we could include:
    # nodes: List[BuildGraphNodeModel] = []

class TaskDefinitionModel(BaseModel):
    """Defines an individual task structure."""
    task_name: str
    command_details: List[str]  # ✅ From `core.task_runner.TASK_COMMANDS`
    description: Optional[str] = None

class TaskListResponse(BaseModel):
    """Response model containing task definitions."""
    tasks: List[TaskDefinitionModel]

# --- Algorithm Processing Models ---

class ApplyAlgorithmRequest(BaseModel):
    """Request model for applying proprietary algorithms."""
    algorithm_id: str  # ✅ e.g., "CABGP", "RBCP"
    project_id: Optional[str] = None
    context_data: Dict[str, Any]

class ApplyAlgorithmResponse(BaseModel):
    """Response model for applied proprietary algorithms."""
    algorithm_id: str
    project_id: Optional[str]
    status: str
    result: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class MCPStrategyApiRequest(BaseModel):
    """Defines request structure for MCP strategy optimization."""
    current_dag_snapshot: Dict[str, Any]
    optimization_goal: Optional[str] = None
    additional_mcp_context: Optional[Dict[str, Any]] = None

class MCPStrategyApiDetails(BaseModel):
    """Defines strategy details from MCP."""
    strategy_id: str
    new_dag_definition_raw: Dict[str, Any]
    directives: Optional[Dict[str, Any]] = None
    mcp_metadata: Optional[Dict[str, Any]] = None

class MCPStrategyApiResponse(BaseModel):
    """Response model for MCP strategy optimization."""
    project_id: str
    status: str
    message: Optional[str] = None
    strategy_details: Optional[MCPStrategyApiDetails] = None
