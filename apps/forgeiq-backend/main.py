# ==========================
# 📁 apps/forgeiq-backend/main.py
# ==========================

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import requests

# === FastAPI App Configuration ===
app = FastAPI(
    title="ForgeIQ Backend",
    description="Autonomous Agent Build System Orchestrator",
    version="0.1.0"
)

# === In-Memory Agent Registry ===
AGENT_REGISTRY = {}

def register_agent(name: str, endpoint: str, capabilities: list):
    AGENT_REGISTRY[name] = {
        "endpoint": endpoint,
        "capabilities": capabilities
    }

def get_agent(name: str):
    return AGENT_REGISTRY.get(name)

def list_agents():
    return AGENT_REGISTRY

def update_heartbeat(name: str):
    if name in AGENT_REGISTRY:
        AGENT_REGISTRY[name]["last_seen"] = datetime.utcnow().isoformat()

# === Pydantic Models ===
class ErrorLogRequest(BaseModel):
    error_log: str

class BuildTriggerRequest(BaseModel):
    project: str
    target: Optional[str] = "build"

class AgentRegistration(BaseModel):
    name: str
    endpoint: str
    capabilities: list

# === API Endpoints ===

@app.get("/health")
def health():
    return {"status": "ForgeIQ backend is healthy"}

@app.post("/diagnose")
async def diagnose(request: ErrorLogRequest):
    # TODO: Route to DebugIQ in future
    if "ReferenceError" in request.error_log:
        return {"diagnosis": "Undefined variable", "confidence": 0.92}
    return {"diagnosis": "Generic error", "confidence": 0.65}

@app.post("/trigger-build")
async def trigger_build(req: BuildTriggerRequest):
    # === PlanAgent Coordination ===
    plan_agent = get_agent("PlanAgent")
    if not plan_agent:
        raise HTTPException(status_code=404, detail="PlanAgent not registered")

    try:
        response = requests.get(
            f"{plan_agent['endpoint']}/plan",
            params={"project": req.project}
        )
        plan = response.json()
        return {
            "status": "Build plan created.",
            "project": req.project,
            "tasks": plan.get("tasks", []),
            "strategy": plan.get("strategy", "N/A")
        }
    except Exception as e:
        return {"error": f"Failed to reach PlanAgent: {str(e)}"}

@app.post("/agent/register")
def agent_register(agent: AgentRegistration):
    register_agent(agent.name, agent.endpoint, agent.capabilities)
    return {"message": f"{agent.name} registered."}

@app.get("/agent/{name}")
def agent_get(name: str):
    agent = get_agent(name)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent

@app.get("/agents")
def agent_list():
    return list_agents()
