"""
utils.py

Support utilities for orchestrator logic including safe summaries, formatting, and helpers.
"""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def message_summary(payload: Optional[Dict[str, Any]]) -> str:
    """
    Generates a brief readable summary from an MCP response or orchestration payload.
    """
    if not payload or not isinstance(payload, dict):
        return "[Invalid or empty payload]"

    keys_to_highlight = ["status", "strategy_id", "message", "dag_id"]
    summary_parts = []

    for key in keys_to_highlight:
        if key in payload:
            value = payload[key]
            summary_parts.append(f"{key}={value}")

        # Support nested strategy details
        elif key in payload.get("strategy_details", {}):
            value = payload["strategy_details"][key]
            summary_parts.append(f"{key}={value}")

    return " | ".join(summary_parts) or f"{list(payload.keys())[:3]}..."
