# core/agent_registry.py
class AgentNotFoundError(Exception):
    """Raised when an agent is not found in the registry."""
    pass


class AgentRegistry:
    """A simple registry to manage agents."""

    def __init__(self):
        self._agents = {}

    def register_agent(self, name, agent_instance):
        """Registers an agent with the given name."""
        if name in self._agents:
            raise ValueError(f"Agent '{name}' is already registered.")
        self._agents[name] = agent_instance

    def get_agent(self, name):
        """Retrieves an agent by name."""
        if name not in self._agents:
            raise AgentNotFoundError(f"Agent '{name}' not found.")
        return self._agents[name]

    def remove_agent(self, name):
        """Removes an agent from the registry."""
        if name not in self._agents:
            raise AgentNotFoundError(f"Agent '{name}' not found.")
        del self._agents[name]

    def list_agents(self):
        """Returns a list of all registered agent names."""
        return list(self._agents.keys())
