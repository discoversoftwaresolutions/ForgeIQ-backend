"""
state.py
from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any
class MyModel(BaseModel):
    model_config = ConfigDict(extra="allow")
Defines orchestration flow state for DAG builds, deployment flows, and event tracking.
"""

class OrchestrationFlowState(BaseModel):
    flow_id: str
    flow_name: str
    project_id: str
    commit_sha: str

    status: str = Field(default="PENDING")  # PENDING | RUNNING | COMPLETED | FAILED
    current_stage: str
    started_at: str  # ISO 8601 UTC timestamp
    updated_at: str  # ISO 8601 UTC timestamp

    last_event_id_processed: Optional[str] = None
    dag_id: Optional[str] = None
    deployment_request_id: Optional[str] = None
    error_message: Optional[str] = None

    context_data: Optional[Dict[str, Any]] = None

    class Config:
        arbitrary_types_allowed = True
        extra = "allow"
