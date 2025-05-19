from typing import Optional

ROUTING_RULES = {
    "agent_status_update": "agent_manager.handle_status_update",
    "deployment_request": "deployment_manager.process_request",
    "cache_lookup": "cache_service.fetch_item",
}

def get_routing_target(route_name: str) -> Optional[str]:
    """Returns the target function for a given route."""
    return ROUTING_RULES.get(route_name)
