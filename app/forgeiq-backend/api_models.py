# File: forgeiq-backend/api_models.py

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
import uuid # Needed for default_factory in Field

# --- Request Models for ForgeIQ Endpoints ---

class ForgeIQTaskStatusResponse(BaseModel):
    task_id: str
    task_type: str
    status: str
    current_stage: Optional[str] = None
    progress: int = 0
    logs: Optional[str] = None
    output_data: Optional[Dict[str, Any]] = None
    details: Optional[Dict[str, Any]] = None



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


# --- SDK Models (used by ForgeIQ's Orchestrator internally) ---
# These are used by the Orchestrator class when calling the private intel stack.
# They are included here for completeness of API models, but might also live in forgeiq_sdk.models
class SDKMCPStrategyRequestContext(BaseModel):
    project_id: str
    current_dag_snapshot: Optional[List[Dict[str, Any]]] = None
    optimization_goal: str
    additional_mcp_context: Dict[str, Any] = {}

class SDKMCPStrategyResponse(BaseModel):
    # This aligns with what the proprietary service is expected to return
    status: str # "strategy_provided", "unavailable"
    message: Optional[str] = None
    strategy_id: Optional[str] = None
    strategy_details: Optional[MCPStrategyApiDetails] = None # Re-using details model

class SDKTaskStatusModel(BaseModel): # Example for SDK response
    task_id: str
    status: str

class SDKDagExecutionStatusModel(BaseModel): # Example for SDK response
    dag_id: str
    status: str

class SDKDeploymentStatusModel(BaseModel): # Example for SDK response
    deployment_id: str
    status: str


# --- ForgeIQ Internal Task Status Model ---
# This is what /forgeiq/status/{forgeiq_task_id} returns
class ForgeIQTaskStatusResponse(BaseModel):
    task_id: str
    task_type: str
    status: str
    current_stage: Optional[str] = None
    progress: int = 0
    logs: Optional[str] = None
    output_data: Optional[Dict[str, Any]] = None
    details: Optional[Dict[str, Any]] = None

# --- Models used by /task_list endpoint ---
class TaskDefinitionModel(BaseModel):
    task_name: str
    command_details: Dict[str, Any]

class TaskListResponse(BaseModel):
    tasks: List[TaskDefinitionModel]

# --- TaskPayload from Autosoft Orchestrator ---
# This model defines the structure of the payload that Autosoft Orchestrator sends to ForgeIQ's /build endpoint
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
