from app.utils.mcp_helper import optimize_dag_with_mcp
from sqlalchemy.orm import Session

async def apply_mcp_to_payload(task_payload, db: Session = None):
    """
    Centralized helper to apply MCP optimization to any TaskPayload.
    Returns the MCP strategy ID if applied, else None.
    """
    mission_data = task_payload.dict().get("mission", {})
    dag_data = task_payload.dict().get("dag", {})

    optimized = await optimize_dag_with_mcp(mission_data, dag_data)
    if optimized:
        task_payload.dag = optimized.optimized_dag
        if db and hasattr(task_payload, "id"):
            # Update DB record if session provided
            task_record = db.get(ForgeIQTask, task_payload.id)
            if task_record:
                task_record.payload["dag"] = optimized.optimized_dag
                task_record.details = {
                    "mcp_strategy_id": optimized.strategy_id,
                    "policies_applied": optimized.policies_applied,
                    "explanation": optimized.explanation
                }
                db.commit()
        return optimized.strategy_id
    return None
