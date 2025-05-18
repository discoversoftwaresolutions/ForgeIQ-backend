from .router import MessageRouter, MessageRouteNotFoundError, InvalidMessagePayloadError
from .routing_rules import get_routing_target
from typing import Optional  
__all__ = ["MessageRouter", "MessageRouteNotFoundError", "InvalidMessagePayloadError", "get_routing_target"]
ROUTING_RULES = {
    "agent_status_update": "agent_manager.handle_status_update",
    "deployment_request": "deployment_manager.process_request",
    "cache_lookup": "cache_service.fetch_item",
}

def get_routing_target(route_name: str) -> str:
    """Returns the target function for a given route."""
    return ROUTING_RULES.get(route_name, "")
