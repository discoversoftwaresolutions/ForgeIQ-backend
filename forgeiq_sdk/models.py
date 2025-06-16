from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class DagDefinition(BaseModel):
    dag_id: str
    nodes: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None


class SDKMCPStrategyRequestContext(BaseModel):
    project_id: str
    current_dag_snapshot: List[Dict[str, Any]]
    optimization_goal: Optional[str]
    additional_mcp_context: Optional[Dict[str, Any]]


class SDKMCPStrategyResponse(BaseModel):
    status: str
    message: Optional[str]
    strategy_details: Optional[Dict[str, Any]]
