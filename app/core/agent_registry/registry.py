class AgentRegistry:
    """Manages agent registrations and lookups."""
    def __init__(self):
        self.registry = {}

    def register_agent(self, agent_id: str, agent_data: dict):
        """Registers a new agent."""
        self.registry[agent_id] = agent_data

    def get_agent(self, agent_id: str) -> dict:
        """Retrieves agent details."""
        return self.registry.get(agent_id, {})
