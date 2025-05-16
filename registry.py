from typing import Dict
from datetime import datetime

# === In-memory registry for now (can evolve to Redis/Postgres) ===
AGENT_REGISTRY: Dict[str, Dict] = {}

def register_agent(name: str, endpoint: str, capabilities: list):
    AGENT_REGISTRY[name] = {
        "endpoint": endpoint,
        "capabilities": capabilities,
        "last_seen": datetime.utcnow().isoformat()
    }

def get_agent(name: str):
    return AGENT_REGISTRY.get(name, None)

def list_agents():
    return AGENT_REGISTRY

def update_heartbeat(name: str):
    if name in AGENT_REGISTRY:
        AGENT_REGISTRY[name]["last_seen"] = datetime.utcnow().isoformat()
