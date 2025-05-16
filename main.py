from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import Optional

app = FastAPI(
    title="ForgeIQ Backend",
    description="Autonomous Agent Build System Orchestrator",
    version="0.1.0"
)

# === Domain Models ===
class ErrorLogRequest(BaseModel):
    error_log: str

class BuildTriggerRequest(BaseModel):
    project: str
    target: Optional[str] = "build"

# === Core Routes ===

@app.post("/diagnose")
async def diagnose(request: ErrorLogRequest):
    # TODO: Route to DebugIQ Agent or analysis pipeline
    return {"diagnosis": "TBD", "confidence": 0.0}

@app.post("/trigger-build")
async def trigger_build(req: BuildTriggerRequest):
    # TODO: Hook PlanAgent + CI_CD Agent
    return {"status": f"Build pipeline triggered for {req.project} targeting {req.target}"}

@app.get("/health")
def health():
    return {"status": "ForgeIQ backend is healthy"}
from registry import register_agent, list_agents, get_agent, update_heartbeat
from fastapi import HTTPException

class AgentRegistration(BaseModel):
    name: str
    endpoint: str
    capabilities: list

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

@app.post("/agent/{name}/heartbeat")
def agent_heartbeat(name: str):
    update_heartbeat(name)
    return {"message": f"Heartbeat updated for {name}"}
