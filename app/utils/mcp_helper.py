# app/utils/mcp_helper.py
import os
import httpx
from typing import Dict, Any, Optional
from app.api_models import MCPOptimizeRequest, MCPOptimizeResponse
import logging

logger = logging.getLogger(__name__)

async def optimize_dag_with_mcp(mission: Dict[str, Any], dag: Dict[str, Any], goal: str = "general_build_efficiency") -> Optional[MCPOptimizeResponse]:
    """
    Sends mission + DAG to MCP optimizer and returns optimized DAG & strategy info.
    Returns None if MCP fails.
    """
    if not mission or not dag:
        return None

    mcp_url = os.getenv("MCP_URL", "http://localhost:8001/mcp/optimize")
    payload = MCPOptimizeRequest(mission=mission, dag=dag, goal=goal).dict()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(mcp_url, json=payload)
            resp.raise_for_status()
            optimized = MCPOptimizeResponse(**resp.json())
            logger.info(f"MCP optimization applied. Strategy ID: {optimized.strategy_id}")
            return optimized
    except Exception as e:
        logger.warning(f"MCP optimization failed: {e}")
        return None
