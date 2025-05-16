# ======================================
# 📁 interfaces/types/agent.py
# ======================================
from typing import TypedDict, List, Dict, Any, Optional

class AgentCapability(TypedDict):
    name: str               # e.g., "python_sast_scan", "dag_execution", "code_embedding"
    version: Optional[str]
    parameters_schema: Optional[Dict[str, Any]] # JSON schema for expected parameters

class AgentEndpoint(TypedDict):
    type: str               # e.g., "http_api", "redis_event_channel"
    address: str            # e.g., "http://agent-x:8000/api", "commands.agent_x.tasks"
    protocol_details: Optional[Dict[str, Any]] # e.g., {"method": "POST"} for http

class AgentRegistrationInfo(TypedDict):
    agent_id: str           # Unique identifier for the agent instance
    agent_type: str         # e.g., "SecurityAgent", "PlanAgent", "CodeNavAgent"
    status: str             # e.g., "active", "inactive", "degraded"
    capabilities: List[AgentCapability]
    endpoints: List[AgentEndpoint] # How to communicate with this agent
    metadata: Optional[Dict[str, Any]] # Other relevant info (version, deployment_id, etc.)
    last_seen_timestamp: str # ISO datetime
