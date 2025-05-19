import logging
from typing import Callable, Dict, List, Any

class HookManager:
    """Manages hooks for event-driven execution."""

    def __init__(self):
        self.hooks: Dict[str, List[Callable[..., Any]]] = {}

    def register_hook(self, event_name: str, handler: Callable[..., Any]) -> None:
        """Registers a hook function for a specific event."""
        if event_name not in self.hooks:
            self.hooks[event_name] = []
        self.hooks[event_name].append(handler)
        logging.info(f"Hook registered for event: {event_name}")

    def trigger_hooks(self, event_name: str, *args, **kwargs) -> None:
        """Triggers all hooks registered under the event name."""
        handlers = self.hooks.get(event_name, [])
        if not handlers:
            logging.warning(f"No hooks found for event: {event_name}")
            return
        
        for handler in handlers:
            try:
                handler(*args, **kwargs)
            except Exception as e:
                logging.error(f"Error in hook {handler.__name__} for event {event_name}: {e}")

    def remove_hook(self, event_name: str, handler: Callable[..., Any]) -> bool:
        """Removes a specific hook function from an event."""
        if event_name in self.hooks and handler in self.hooks[event_name]:
            self.hooks[event_name].remove(handler)
            logging.info(f"Hook removed for event: {event_name}")
            return True
        return False

    def list_hooks(self) -> Dict[str, List[str]]:
        """Lists all registered hooks and their handlers."""
        return {event: [handler.__name__ for handler in handlers] for event, handlers in self.hooks.items()}
