"""
state.py

Defines orchestration flow state using Pydantic v2 models.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, ConfigDict


class OrchestrationFlowState(BaseModel):
    flow_id: str
    flow_name: str
    project_id: str
    commit_sha: str

    status: str = "PENDING"  # e.g., PENDING | RUNNING | COMPLETED | FAILED
    current_stage: str
    started_at: str  # ISO8601 string
    updated_at: str

    last_event_id_processed: Optional[str] = None
    dag_id: Optional[str] = None
    deployment_request_id: Optional[str] = None
    error_message: Optional[str] = None

    context_data: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="allow")
