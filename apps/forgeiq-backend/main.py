# Rebuild forgeiq-backend main.py with full logic including task execution and agent orchestration
from pathlib import Path

main_py_path = Path("/mnt/data/forgeiq/apps/forgeiq-backend/main.py")

full_main_py = """# ==========================
# 📁 apps/forgeiq-backend/main.py
# ==========================

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, List
from datetime import datetime
import requests

# === FastAPI App ===
app = FastAPI(
    title="ForgeIQ Backend",
    description="Autonomous Agent Build System Orchestrator",
    version="0.1.0"
)

# === Agent Registry (in-memory) ===
AGENT_REGISTRY: Dict[str, Dict] = {}

def register_agent(name: str, endpoint: str, capabilities: List[str]):
    AGENT_REGISTRY[name] = {
        "endpoint": endpoint,
        "capabilities": capabilities,
        "last_seen": datetime.utcnow().isoformat()
    }

def get_agent(name: str):
    return AGENT_REGISTRY.get(name)

def list_agents():
    return AGENT_REGISTRY

def update_heartbeat(name: str):
    if name in AGENT_REGISTRY:
        AGENT_REGISTRY[name]["last_seen"] = datetime.utcnow().isoformat()

# === Request Models ===
class ErrorLogRequest(BaseModel):
    error_log: str

class BuildTriggerRequest(BaseModel):
    project: str
    target: Optional[str] = "build"

class AgentRegistration(BaseModel):
    name: str
    endpoint: str
    capabilities: List[str]

class TaskExecutionRequest(BaseModel):
    project: str
    tasks: List[str]

# === API Routes ===

@app.get("/health")
def health():
    return {"status": "ForgeIQ backend is healthy"}

@app.post("/diagnose")
async def diagnose(request: ErrorLogRequest):
    if "ReferenceError" in request.error_log:
        return {"diagnosis": "Undefined variable", "confidence": 0.92}
    return {"diagnosis": "Generic error", "confidence": 0.65}

@app.post("/trigger-build")
async def trigger_build(req: BuildTriggerRequest):
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

@app.post("/execute-tasks")
async def execute_tasks(req: TaskExecutionRequest):
    results = []
    for task in req.tasks:
        agent_name = f"{task.lower()}-agent".replace("_", "-")
        agent = get_agent(agent_name.capitalize())
        if not agent:
            results.append({
                "task": task,
                "status": "skipped",
                "reason": f"{agent_name} not registered"
            })
            continue
        try:
            exec_resp = requests.post(f"{agent['endpoint']}/execute", json={"project": req.project, "task": task})
            results.append({
                "task": task,
                "status": "completed",
                "result": exec_resp.json()
            })
        except Exception as e:
            results.append({
                "task": task,
                "status": "failed",
                "reason": str(e)
            })
    return {"project": req.project, "results": results}

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
"""

pipeline_logic = """
@app.post("/pipeline")
async def full_pipeline(req: BuildTriggerRequest):
    try:
        # Step 1: Get build plan from PlanAgent
        plan_agent = get_agent("PlanAgent")
        if not plan_agent:
            raise HTTPException(status_code=404, detail="PlanAgent not registered")
        response = requests.get(f"{plan_agent['endpoint']}/plan", params={"project": req.project})
        plan = response.json()
        tasks = plan.get("tasks", [])

        # Step 2: Execute each task
        results = []
        for task in tasks:
            agent_name = f"{task.lower()}-agent".replace("_", "-")
            agent = get_agent(agent_name.capitalize())
            if not agent:
                results.append({
                    "task": task,
                    "status": "skipped",
                    "reason": f"{agent_name} not registered"
                })
                continue
            try:
                exec_resp = requests.post(f"{agent['endpoint']}/execute", json={"project": req.project, "task": task})
                results.append({
                    "task": task,
                    "status": "completed",
                    "result": exec_resp.json()
                })
            except Exception as e:
                results.append({
                    "task": task,
                    "status": "failed",
                    "reason": str(e)
                })

        return {
            "project": req.project,
            "strategy": plan.get("strategy", "N/A"),
            "results": results
        }

    except Exception as e:
        return {"error": f"Pipeline execution failed: {str(e)}"}
"""
Only append if not already present
if "async def full_pipeline" not in main_content:
    main_content = main_content.strip() + "\n\n" + pipeline_logic
    main_path.write_text(main_content)
