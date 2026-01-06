from fastapi import APIRouter, HTTPException
from shared.contracts.mission import MissionContext
from pydantic import BaseModel
from typing import Dict, Any
import uuid

router = APIRouter(prefix="/intellicore/cognition", tags=["Cognition"])


class CognitionPlanResponse(BaseModel):
    mission_id: str
    reasoning_graph_id: str
    execution_plan: Dict[str, Any]
    requires_mcp_optimization: bool


@router.post("/plan", response_model=CognitionPlanResponse)
async def plan_cognition(mission: MissionContext):
    """
    Converts natural language intent into an executable cognition plan.
    """
    reasoning_graph_id = f"rg-{uuid.uuid4().hex[:6]}"

    execution_plan = {
        "plan_id": f"plan-{uuid.uuid4().hex[:6]}",
        "steps": [
            {
                "step_id": "s1",
                "action": "analyze_current_dag",
                "agent": "deployment_analyst",
            },
            {
                "step_id": "s2",
                "action": "propose_optimized_dag",
                "agent": "architecture_agent",
            },
        ],
        "estimated_cost_delta": "-18%",
        "risk_summary": "Reduced blast radius, stricter IAM boundaries",
    }

    return CognitionPlanResponse(
        mission_id=mission.mission_id,
        reasoning_graph_id=reasoning_graph_id,
        execution_plan=execution_plan,
        requires_mcp_optimization=True,
    )
