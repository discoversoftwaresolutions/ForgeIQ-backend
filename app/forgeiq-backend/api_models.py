from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
import uuid # Needed for default_factory in Field

# --- Request Models for ForgeIQ Endpoints ---

# UserPromptData model
class UserPromptData(BaseModel):
    """
    Model representing a user's prompt and associated context
    for various AI-driven operations.
    """
    prompt: str = Field(..., description="The natural language prompt from the user.")
    # ADDED: This field now correctly matches the 'history' array sent by the frontend
    history: List[Dict[str, str]] = Field([], description="Conversation history, list of {'role': 'user'|'assistant', 'content': 'message'}")
    
    # MODIFIED: Made these fields Optional as they are not currently sent by the frontend's /gateway calls
    project_id: Optional[str] = Field(None, description="The ID of the project this prompt relates to.")
    user_id: Optional[str] = Field(None, description="Optional ID of the user submitting the prompt.")
    context_data: Dict[str, Any] = Field({}, description="Additional context or metadata related to the prompt.")

class CodeGenerationRequest(BaseModel):
    """
    Request model for the /code_generation endpoint.
    """
    project_id: str
    prompt_text: str
    config_options: Dict[str, Any] = {}

class PipelineGenerateRequest(BaseModel):
    """
    Request model for the /pipeline_generate endpoint.
    """
    project_id: str
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    pipeline_type: str
    context: Dict[str, Any] = {}

class DeploymentTriggerRequest(BaseModel):
    """
    Request model for the /deploy_service endpoint.
    """
    service_name: str
    project_id: str
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    artifact_url: Optional[str] = None
    deployment_env: Optional[str] = None

class ApplyAlgorithmRequest(BaseModel):
    """
    Request model for the /api/forgeiq/algorithms/apply endpoint.
    """
    algorithm_id: str # e.g., "CABGP", "LLM_OPTIMIZED_DAG"
    project_id: str
    context_data: Dict[str, Any]

class MCPStrategyApiRequest(BaseModel):
    """
    Request model for the /api/forgeiq/mcp/optimize-strategy/{project_id} endpoint.
    """
    current_dag_snapshot: Optional[List[Dict[str, Any]]] = None # Simplified; could be Pydantic model for DAG nodes
    optimization_goal: str = "general_build_efficiency"
    additional_mcp_context: Dict[str, Any] = {}

# --- Response Models for ForgeIQ Endpoints ---

class ProjectConfigResponse(BaseModel):
    """
    Response model for project configuration details.
    """
    project_id: str
    name: str
    config_version: str
    details: Dict[str, Any]

class PipelineGenerateResponse(BaseModel):
    status: str
    request_id: str
    pipeline_id: Optional[str] = None

class DeploymentTriggerResponse(BaseModel):
    status: str
    request_id: str
    deployment_status: Optional[str] = None

class ApplyAlgorithmResponse(BaseModel):
    status: str
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None

class MCPStrategyApiDetails(BaseModel):
    strategy_id: str
    new_dag_definition_raw: Optional[Dict[str, Any]] = None # Raw JSON dict of the new DAG
    directives: Optional[List[str]] = None
    mcp_metadata: Optional[Dict[str, Any]] = None

class MCPStrategyApiResponse(BaseModel):
    project_id: str
    status: str # e.g., "strategy_provided", "no_strategy"
    message: Optional[str] = None
    strategy_details: Optional[MCPStrategyApiDetails] = None


# SDK Models (used by ForgeIQ's Orchestrator internally or external SDKs)
class SDKMCPStrategyRequestContext(BaseModel):
    project_id: str
    current_dag_snapshot: Optional[List[Dict[str, Any]]] = None
    optimization_goal: str
    additional_mcp_context: Dict[str, Any] = {}

class SDKMCPStrategyResponse(BaseModel):
    status: str # "strategy_provided", "unavailable"
    message: Optional[str] = None
    strategy_id: Optional[str] = None
    strategy_details: Optional[MCPStrategyApiDetails] = None

class SDKTaskStatusModel(BaseModel): # Example for SDK response
    task_id: str
    status: str

class SDKDagExecutionStatusModel(BaseModel): # Example for SDK response
    dag_id: str
    status: str

class SDKDeploymentStatusModel(BaseModel): # Example for SDK response
    deployment_id: str
    status: str

# BuildGraphNodeModel (Conceptual model for a node within a DAG)
class BuildGraphNodeModel(BaseModel):
    """
    A conceptual model for a node (task) within a build DAG.
    Aligns with expected DagNode fields for LLM generation/parsing.
    """
    id: str = Field(..., description="Unique task ID within the DAG.")
    task_type: str = Field(..., description="Type of task (e.g., 'lint', 'test', 'build', 'deploy').")
    command: Optional[List[str]] = Field(None, description="Command to execute this task.")
    agent_handler: Optional[str] = Field(None, description="Agent responsible for handling this task (e.g., 'SecurityAgent').")
    params: Dict[str, Any] = Field({}, description="Parameters specific to this task.")
    dependencies: List[str] = Field([], description="List of IDs of tasks this node depends on.")
    # Add any other fields you use in your DAG nodes, e.g., 'priority', 'status', 'estimated_duration'

# NEW: BuildGraphResponse (A response model that might contain a DAG structure)
class BuildGraphResponse(BaseModel):
    """
    Response model containing a generated or optimized build graph (DAG).
    """
    dag_id: str = Field(..., description="ID of the generated/optimized DAG.")
    project_id: str = Field(..., description="ID of the project the DAG belongs to.")
    description: Optional[str] = Field(None, description="Description of the DAG.")
    nodes: List[BuildGraphNodeModel] = Field([], description="List of nodes (tasks) in the DAG.")
    status: str = Field(..., description="Status of the build graph generation/optimization.")
    message: Optional[str] = Field(None, description="A message related to the response.")


# ForgeIQ Internal Task Status Model
class ForgeIQTaskStatusResponse(BaseModel):
    task_id: str
    task_type: str
    status: str
    current_stage: Optional[str] = None
    progress: int = 0
    logs: Optional[str] = None
    output_data: Optional[Dict[str, Any]] = None # This will contain the 'llm_response'
    details: Optional[Dict[str, Any]] = None

# Models used by /task_list endpoint
class TaskDefinitionModel(BaseModel):
    task_name: str
    command_details: Dict[str, Any]

class TaskListResponse(BaseModel):
    tasks: List[TaskDefinitionModel]

# TaskPayload from Autosoft Orchestrator
class TaskPayloadFromOrchestrator(BaseModel):
    task_id: str # This is the Orchestrator's task_id
    type: str # e.g., "orchestrate", "build"
    payload: Dict[str, Any] # The actual payload relevant to ForgeIQ (e.g., project_id, prompt_text)
    status: str = "pending"
    logs: str = ""
    output_url: str = ""
    current_stage: str = None
    progress: int = 0
    details: Dict[str, Any] = None
