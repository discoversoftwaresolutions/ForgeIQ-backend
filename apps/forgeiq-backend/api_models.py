# ================================================
# 📁 apps/forgeiq-backend/api_models.py
# ================================================
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
import uuid # For default factory in request_id

# --- Request Models ---

class UserPromptData(BaseModel):
    prompt_text: str
    target_project_id: Optional[str] = None
    additional_context: Optional[Dict[str, Any]] = None

class PipelineGenerateRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    user_prompt_data: UserPromptData

class DeploymentTriggerRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    service_name: str
    commit_sha: str
    target_environment: str
    triggered_by: Optional[str] = "API"

# --- Response Models ---

class PipelineGenerateResponse(BaseModel):
    message: str
    request_id: str
    status: str = "accepted"

class DeploymentTriggerResponse(BaseModel):
    message: str
    request_id: str
    status: str = "accepted"

class SDKTaskStatusModel(BaseModel):
    task_id: str
    status: str
    message: Optional[str] = None
    result_summary: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

class SDKDagExecutionStatusModel(BaseModel):
    dag_id: str
    project_id: Optional[str] = None
    status: str
    message: Optional[str] = None
    started_at: Optional[str] = None # Making optional to handle NOT_FOUND case where it might not be set
    completed_at: Optional[str] = None
    task_statuses: List[SDKTaskStatusModel] = []

class SDKDeploymentStatusModel(BaseModel):
    deployment_id: Optional[str] = None # This might not be known if status is e.g. PENDING_TRIGGER
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
    project_id: str
    configuration: Dict[str, Any] # Or define a more specific Pydantic model for the config structure

class BuildGraphNodeModel(BaseModel):
    id: str
    task_type: str # Or 'name'
    dependencies: List[str]
    # Add other fields as needed, e.g., command, params, if your core.build_graph provides them

class BuildGraphResponse(BaseModel):
    project_id: str
    tasks_sequence: List[str] # Based on current core.build_graph.get_project_dag output
    # If get_project_dag returned a more complex structure, this model would change:
    # nodes: List[BuildGraphNodeModel] = []
    # For now, aligning with List[str] output.

class TaskDefinitionModel(BaseModel):
    task_name: str
    command_details: List[str] # From core.task_runner.TASK_COMMANDS
    description: Optional[str] = None

class TaskListResponse(BaseModel):
    tasks: List[TaskDefinitionModel]

# You can add more specific request/response models here as your API evolves.
# For example, if POST/PUT endpoints expect specific data for creation/update.
# In apps/forgeiq-backend/app/api_models.py
class ApplyAlgorithmRequest(BaseModel):
    algorithm_id: str # e.g., "CABGP", "RBCP"
    project_id: Optional[str] = None
    context_data: Dict[str, Any]

class ApplyAlgorithmResponse(BaseModel):
    algorithm_id: str
    project_id: Optional[str]
    status: str
    result: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
