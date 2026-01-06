from fastapi import APIRouter, HTTPException
from shared.contracts.mission import MissionContext
from pydantic import BaseModel
from typing import Dict, Any, List

from agents.MCP.strategy import MCPStrategyEngine
from agents.MCP.governance_bridge import send_proprietary_audit_event

router = APIRouter(prefix="/mcp/strategy", tags=["MCP"])

strategy_engine = MCPStrategyEngine()


class MCPOptimizationRequest(BaseModel):
    mission_context: MissionContext
    reasoning_graph_id: str
    current_dag: Dict[str, Any]
    optimization_goal: str


@router.post("/optimize")
async def optimize_strategy(request: MCPOptimizationRequest):
    """
    Applies policy-aware optimization to execution DAGs.
    """

    result = strategy_engine.optimize(
        mission=request.mission_context,
        dag=request.current_dag,
        goal=request.optimization_goal,
    )

    if not result:
        raise HTTPException(status_code=400, detail="No valid optimization produced")

    send_proprietary_audit_event(
        mission_id=request.mission_context.mission_id,
        strategy_id=result["strategy_id"],
        policies_applied=result["policies_applied"],
        outcome="approved_with_modifications",
    )

    return {
        "status": "strategy_provided",
        "strategy_details": {
            "strategy_id": result["strategy_id"],
            "new_dag_definition_raw": result["optimized_dag"],
            "explanation": result["explanation"],
        },
    }
