# ========================================
# 📁 core/agent_registry/registry.py
# ========================================
import logging
import datetime # Standard library import for timestamps
from typing import Dict, List, Optional, Any # Standard library import for typing
from threading import RLock # Standard library import for thread-safety

# Attempt to import OpenTelemetry for tracing, but make it optional
_tracer = None
_trace_api = None
try:
    from opentelemetry import trace as otel_trace_api # Renamed to avoid conflict
    _tracer = otel_trace_api.get_tracer(__name__)
    _trace_api = otel_trace_api # To access trace.Status, trace.StatusCode
except ImportError:
    logging.getLogger(__name__).info(
        "OpenTelemetry API not found. AgentRegistry will operate without distributed tracing."
    )

# Project-specific imports. These rely on:
# 1. The 'interfaces' directory being a Python package (contains __init__.py).
# 2. The 'interfaces/types/' directory being a Python package (contains __init__.py).
# 3. The 'interfaces/types/agent.py' file existing and defining these types.
# 4. The Python execution environment (e.g., Docker container with PYTHONPATH=/app)
#    being able to find the 'interfaces' package from the 'core' package.
from interfaces.types.agent import AgentRegistrationInfo # AgentCapability and AgentEndpoint are used via AgentRegistrationInfo

logger = logging.getLogger(__name__)

class AgentNotFoundError(Exception):
    """Custom exception for when an agent is not found in the registry."""
    pass

class AgentRegistry:
    def __init__(self):
        """
        Initializes an in-memory agent registry.
        For production with multiple instances, a persistent backend (e.g., Redis) would be needed.
        """
        self._registry: Dict[str, AgentRegistrationInfo] = {}
        self._lock = RLock() # To make operations on _registry thread-safe
        logger.info("AgentRegistry initialized (in-memory backend).")

    def _start_trace_span_if_available(self, operation_name: str, **extra_attrs):
        """Helper to start a trace span only if tracing is properly initialized."""
        if _tracer and _trace_api: # Check both
            span = _tracer.start_span(f"agent_registry.{operation_name}")
            for k, v in extra_attrs.items():
                span.set_attribute(k, v)
            return span

        # Return a no-op context manager if no tracer
        class NoOpSpan:
            def __enter__(self): return self
            def __exit__(self, exc_type, exc_val, exc_tb): pass
            def set_attribute(self, key_attr, value_attr): pass
            def record_exception(self, exception): pass
            def set_status(self, status): pass
            def end(self): pass
        return NoOpSpan()

    def register_agent(self, agent_info: AgentRegistrationInfo) -> bool:
        """
        Registers an agent or updates its existing registration.
        The agent_info is expected to be a complete AgentRegistrationInfo dictionary.
        """
        agent_id = agent_info.get("agent_id")
        if not agent_id:
            logger.error("Failed to register agent: agent_id is missing in agent_info.")
            return False

        span = self._start_trace_span_if_available("register_agent", agent_id=agent_id, agent_type=str(agent_info.get("agent_type")))
        try:
            with span: # type: ignore
                with self._lock:
                    # Ensure last_seen_timestamp is current and correctly formatted
                    current_timestamp = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"

                    # Create a new dict to ensure all keys from AgentRegistrationInfo are potentially there,
                    # using defaults where appropriate if not provided in agent_info, though TypedDict
                    # doesn't enforce defaults itself. This assumes agent_info is mostly complete.
                    registration_data: AgentRegistrationInfo = {
                        "agent_id": agent_id,
                        "agent_type": agent_info.get("agent_type", "UnknownAgentType"),
                        "status": agent_info.get("status", "initializing"),
                        "capabilities": agent_info.get("capabilities", []),
                        "endpoints": agent_info.get("endpoints", []),
                        "metadata": agent_info.get("metadata"), # Can be None
                        "last_seen_timestamp": current_timestamp
                    }
                    self._registry[agent_id] = registration_data

                logger.info(f"Agent registered/updated: ID='{agent_id}', Type='{registration_data['agent_type']}'")
                if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                self._trigger_lifecycle_hook("on_agent_registered", registration_data)
                return True
        except Exception as e:
            logger.error(f"Error registering agent '{agent_id}': {e}", exc_info=True)
            if _tracer and _trace_api: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"Registration failed: {str(e)}"))
        return False

    def unregister_agent(self, agent_id: str) -> bool:
        """Removes an agent from the registry."""
        span = self._start_trace_span_if_available("unregister_agent", agent_id=agent_id)
        try:
            with span: # type: ignore
                with self._lock:
                    if agent_id in self._registry:
                        removed_agent_info = self._registry.pop(agent_id)
                        logger.info(f"Agent unregistered: ID='{agent_id}'")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                        self._trigger_lifecycle_hook("on_agent_unregistered", removed_agent_info) # Pass full info
                        return True
                    else:
                        logger.warning(f"Attempted to unregister non-existent agent: ID='{agent_id}'")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Agent not found for unregistration"))
                        return False
        except Exception as e:
            logger.error(f"Error unregistering agent '{agent_id}': {e}", exc_info=True)
            if _tracer and _trace_api: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"Unregistration failed: {str(e)}"))
        return False

    def get_agent(self, agent_id: str) -> Optional[AgentRegistrationInfo]:
        """Retrieves registration information for a specific agent."""
        span = self._start_trace_span_if_available("get_agent", agent_id=agent_id)
        try:
            with span: # type: ignore
                with self._lock:
                    # Return a copy to prevent external modification of the registry's internal state
                    agent_info = self._registry.get(agent_id)
                    agent_info_copy = agent_info.copy() if agent_info else None

                if agent_info_copy:
                    logger.debug(f"Retrieved agent info for ID='{agent_id}'")
                    if _tracer: span.set_attribute("agent.found", True)
                    if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                else:
                    logger.debug(f"Agent info not found for ID='{agent_id}'")
                    if _tracer: span.set_attribute("agent.found", False)
                    # Not finding an agent is not necessarily an error for get_agent itself
                    if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK)) # Or set based on expectation
                return agent_info_copy
        except Exception as e:
            logger.error(f"Error getting agent '{agent_id}': {e}", exc_info=True)
            if _tracer and _trace_api: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"Get agent failed: {str(e)}"))
        return None

    def discover_agents(self,
                        agent_type: Optional[str] = None,
                        capability_name: Optional[str] = None,
                        status: Optional[str] = "active") -> List[AgentRegistrationInfo]:
        """Discovers agents based on type, capability, and/or status."""
        span = self._start_trace_span_if_available("discover_agents", 
                                             agent_type=str(agent_type or "any"), 
                                             capability_name=str(capability_name or "any"), 
                                             status=str(status or "any"))
        try:
            with span: # type: ignore
                with self._lock:
                    # Create a list of copies for safe iteration and external use
                    all_agents_copy = [agent.copy() for agent in self._registry.values()]

                filtered_agents: List[AgentRegistrationInfo] = []
                for agent_info in all_agents_copy:
                    match = True
                    if status and agent_info.get("status") != status:
                        match = False
                    if agent_type and agent_info.get("agent_type") != agent_type:
                        match = False
                    if capability_name:
                        agent_capabilities = agent_info.get("capabilities", [])
                        if not any(cap.get("name") == capability_name for cap in agent_capabilities):
                            match = False

                    if match:
                        filtered_agents.append(agent_info)

                logger.info(f"Discovery: Type='{agent_type}', Capability='{capability_name}', Status='{status}' found {len(filtered_agents)} agent(s).")
                if _tracer: span.set_attribute("discovery.results_count", len(filtered_agents))
                if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                return filtered_agents
        except Exception as e:
            logger.error(f"Error during agent discovery: {e}", exc_info=True)
            if _tracer and _trace_api: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"Discovery failed: {str(e)}"))
        return []

    def list_all_agents(self) -> List[AgentRegistrationInfo]:
        """Returns a list of copies of all registered agents."""
        span = self._start_trace_span_if_available("list_all_agents")
        try:
            with span: # type: ignore
                with self._lock:
                    # Return copies to prevent external modification
                    agents_list = [agent.copy() for agent in self._registry.values()]
                if _tracer: span.set_attribute("discovery.results_count", len(agents_list))
                if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                return agents_list
        except Exception as e:
            logger.error(f"Error listing all agents: {e}", exc_info=True)
            if _tracer and _trace_api: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"List all failed: {str(e)}"))
        return []

    def update_agent_heartbeat(self, agent_id: str, new_status: Optional[str] = "active") -> bool:
        """Updates the agent's last_seen_timestamp and optionally its status."""
        span = self._start_trace_span_if_available("update_agent_heartbeat", agent_id=agent_id, new_status=str(new_status))
        try:
            with span: # type: ignore
                with self._lock:
                    if agent_id in self._registry:
                        if new_status: # Only update status if a new one is provided
                            self._registry[agent_id]["status"] = new_status
                        self._registry[agent_id]["last_seen_timestamp"] = datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z"
                        logger.debug(f"Heartbeat updated for agent '{agent_id}'. Status: '{self._registry[agent_id]['status']}'.")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                        return True
                    else:
                        logger.warning(f"Cannot update heartbeat for non-existent agent: ID='{agent_id}'")
                        if _tracer and _trace_api: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Agent not found for heartbeat"))
                        return False
        except Exception as e:
            logger.error(f"Error updating heartbeat for agent '{agent_id}': {e}", exc_info=True)
            if _tracer and _trace_api: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, f"Heartbeat update failed: {str(e)}"))
        return False

    # --- Lifecycle Hook Placeholders ---
    def _trigger_lifecycle_hook(self, hook_name: str, *args, **kwargs):
        """Internal helper to call lifecycle methods if they exist or publish events."""
        # For V0.1, just log. V0.2 could use a callback system or publish events.
        if hook_name == "on_agent_registered":
            agent_info = args[0] if args else None
            logger.info(f"LIFECYCLE: Agent Registered - ID: {agent_info.get('agent_id') if agent_info else 'N/A'}")
            # Example: self.event_bus.publish("system.agent_registry.agent_registered", {"agent_id": agent_info['agent_id']})
        elif hook_name == "on_agent_unregistered":
            agent_info_or_id = args[0] if args else None
            agent_id_str = agent_info_or_id.get('agent_id') if isinstance(agent_info_or_id, dict) else str(agent_info_or_id)
            logger.info(f"LIFECYCLE: Agent Unregistered - ID: {agent_id_str}")
    ```
