from fastapi import FastAPI
import requests

app = FastAPI(
    title="PlanAgent",
    description="Responsible for DAG planning and build path generation.",
    version="0.1.0"
)

AGENT_NAME = "PlanAgent"
REGISTRY_URL = "http://localhost:8000/agent/register"

@app.on_event("startup")
def register_agent():
    payload = {
        "name": AGENT_NAME,
        "endpoint": "http://localhost:8010",
        "capabilities": ["dag-planning", "target-resolution"]
    }
    try:
        res = requests.post(REGISTRY_URL, json=payload)
        print(f"[{AGENT_NAME}] Registered with ForgeIQ: {res.json()}")
    except Exception as e:
        print(f"[{AGENT_NAME}] Registration failed: {e}")

@app.get("/plan")
def generate_build_plan(project: str):
    # TODO: Integrate with real DAG logic
    return {
        "project": project,
        "tasks": ["lint", "test", "build"],
        "strategy": "linear"
    }
