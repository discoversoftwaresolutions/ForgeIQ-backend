from typing import Dict, Any, Optional

class MessageRouteNotFoundError(Exception):
    """Raised when a requested message route does not exist."""
    pass

class InvalidMessagePayloadError(Exception):
    """Raised when an invalid payload is provided to the router."""
    pass

class MessageRouter:
    """Handles routing messages between agents."""

    def __init__(self):
        self._routes: Dict[str, Any] = {}

    def register_route(self, route_name: str, handler: Any):
        """Registers a message route handler."""
        self._routes[route_name] = handler

    def get_route(self, route_name: str) -> Any:
        """Retrieves the handler for a given route."""
        if route_name not in self._routes:
            raise MessageRouteNotFoundError(f"Route '{route_name}' not found.")
        return self._routes[route_name]

    def send_message(self, route_name: str, payload: Dict[str, Any]) -> Optional[Any]:
        """Sends a message to a registered route."""
        handler = self.get_route(route_name)
        if not isinstance(payload, dict):
            raise InvalidMessagePayloadError("Payload must be a dictionary.")
        return handler(payload)  # Calls the registered handler function with the payload
