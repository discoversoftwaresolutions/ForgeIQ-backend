# =============================================
# 📁 interfaces/types/orchestration.py
# =============================================
from typing import TypedDict, Optional, List, Dict, Any
from .common import Status # Assuming Status is in common.py

class OrchestrationFlowState(TypedDict):
    flow_id: str
    flow_name: str # e.g., "full_build_flow"
    project_id: str
    commit_sha: str
    status: Status # PENDING, RUNNING, COMPLETED_SUCCESS, FAILED
    current_stage: str # e.g., "AWAITING_AFFECTED_TASKS", "AWAITING_DAG_GENERATION", "AWAITING_DAG_EXECUTION", etc.
    started_at: str # ISO datetime
    updated_at: str # ISO datetime
    last_event_id_processed: Optional[str]
    dag_id: Optional[str] # If a DAG is involved
    deployment_request_id: Optional[str] # If a deployment is triggered
    error_message: Optional[str]
    context_data: Optional[Dict[str, Any]] # For storing any other relevant data during the flow
