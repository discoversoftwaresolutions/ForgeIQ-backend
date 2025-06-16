import os
import json
import datetime
import uuid
import logging
import asyncio
import subprocess
import httpx
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, HTTPException, Body, Query, Depends, Security
from fastapi.responses import JSONResponse

# ✅ Local imports
from .auth import get_api_key, get_private_intel_client
from .index import TASK_COMMANDS
from .mcp import MCPProcessor
from .algorithm import AlgorithmExecutor
from .api_models import (
    CodeGenerationRequest,
    PipelineGenerateRequest,
    DeploymentTriggerRequest,
    PipelineGenerateResponse,
    DeploymentTriggerResponse,
    SDKTaskStatusModel,
    SDKDagExecutionStatusModel,
    SDKDeploymentStatusModel,
    ProjectConfigResponse,
    BuildGraphResponse,
    TaskListResponse,
    ApplyAlgorithmRequest,
    ApplyAlgorithmResponse,
    MCPStrategyApiRequest,
    MCPStrategyApiResponse,
    MCPStrategyApiDetails,
    TaskDefinitionModel
)

# ✅ Logging config
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("forgeiq-backend")

# ✅ App init
app = FastAPI(
    title="ForgeIQ Backend",
    description="Agentic intelligence and orchestration for engineering pipelines.",
    version="1.0.0"
)

mcp_processor = MCPProcessor()
algorithm_executor = AlgorithmExecutor()

# ✅ Browser-accessible root route
@app.get("/")
def root():
    return {
        "message": "✅ ForgeIQ Backend is live.",
        "docs": "/docs",
        "status": "/status",
        "version": app.version
    }

# ✅ Health/status check
@app.get("/status", tags=["System"])
def api_status():
    return {
        "status": "online",
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "version": app.version
    }

@app.post("/code_generation")
async def generate_code(request: CodeGenerationRequest):
    logger.info(f"Processing code generation for project '{request.project_id}' using Codex CLI")
    try:
        process = subprocess.run(
            ["codex-cli", "--prompt", request.prompt_text, "--config", json.dumps(request.config_options)],
            capture_output=True,
            text=True,
            check=True
        )
        return {"status": "success", "generated_code": process.stdout.strip()}
    except subprocess.CalledProcessError as e:
        logger.error(f"Codex CLI failed: {e.stderr.strip()}")
        raise HTTPException(status_code=500, detail=f"Codex CLI error: {e.stderr.strip()}")

@app.post("/api/forgeiq/mcp/optimize-strategy/{project_id}", response_model=MCPStrategyApiResponse)
async def mcp_optimize_strategy_endpoint(
    project_id: str,
    request_data: MCPStrategyApiRequest,
    intel_stack_client: httpx.AsyncClient = Depends(get_private_intel_client),
    api_key: str = Depends(get_api_key)
):
    if not intel_stack_client:
        raise HTTPException(status_code=503, detail="MCP service client unavailable.")
    try:
        response = await intel_stack_client.post("/mcp/request-strategy", json={
            "project_id": project_id,
            "current_dag_snapshot": request_data.current_dag_snapshot,
            "optimization_goal": request_data.optimization_goal,
            "additional_context": request_data.additional_mcp_context
        })
        response.raise_for_status()
        payload = response.json()
        details = None
        if payload.get("status") == "strategy_provided":
            details = MCPStrategyApiDetails(
                strategy_id=payload.get("strategy_id"),
                new_dag_definition_raw=payload.get("new_dag_definition"),
                directives=payload.get("directives"),
                mcp_metadata=payload.get("mcp_execution_details")
            )
        return MCPStrategyApiResponse(
            project_id=project_id,
            status=payload.get("status"),
            message=payload.get("message"),
            strategy_details=details
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"MCP error: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MCP exception: {str(e)}")

@app.post("/api/forgeiq/algorithms/apply", response_model=ApplyAlgorithmResponse)
async def apply_proprietary_algorithm_endpoint(
    request_data: ApplyAlgorithmRequest,
    intel_stack_client: httpx.AsyncClient = Depends(get_private_intel_client),
    api_key: str = Depends(get_api_key)
):
    try:
        response = await intel_stack_client.post("/invoke_proprietary_algorithm", json={
            "algorithm_id": request_data.algorithm_id,
            "project_id": request_data.project_id,
            "context_data": request_data.context_data
        })
        response.raise_for_status()
        return ApplyAlgorithmResponse(**response.json())
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Algorithm service error: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calling private algorithm: {str(e)}")

@app.post("/pipeline_generate")
async def generate_pipeline(request: PipelineGenerateRequest):
    logger.info(f"Generating pipeline for project '{request.project_id}'")
    return {"status": "accepted", "request_id": request.request_id}

@app.post("/deploy_service")
async def deploy_service(request: DeploymentTriggerRequest):
    logger.info(f"Triggering deployment for '{request.service_name}'")
    return {"status": "accepted", "request_id": request.request_id}

@app.get("/task_list", response_model=TaskListResponse)
def get_tasks():
    tasks = [TaskDefinitionModel(task_name=k, command_details=v) for k, v in TASK_COMMANDS.items()]
    return TaskListResponse(tasks=tasks)

# ✅ Lifecycle events
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting ForgeIQ Backend...")
    yield
    logger.info("🛑 Shutting down ForgeIQ Backend...")

app.router.lifespan_context = lifespan
