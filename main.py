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
