import os
import json
import datetime
import uuid
import logging
import asyncio
import subprocess
import httpx
from contextlib import asynccontextmanager  # ✅ For lifespan events
from typing import Dict, Any, Optional, List
from .auth import get_api_key  # ✅ Ensure correct import reference
from .auth import get_private_intel_client  # ✅ Ensure correct import reference

# ✅ FastAPI Dependencies
from fastapi import FastAPI, HTTPException, Body, Query, Depends, Security

# ✅ Project Imports
from .index import TASK_COMMANDS
from .mcp import MCPProcessor  # ✅ Importing MCP module
from .algorithm import AlgorithmExecutor  # ✅ Importing algorithm module
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
    MCPStrategyApiDetails
)

# ✅ Logger Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ✅ Initialize FastAPI App
app = FastAPI()

# ✅ Initialize MCP & Algorithm Modules
mcp_processor = MCPProcessor()
algorithm_executor = AlgorithmExecutor()

# --- 🚀 FastAPI Endpoints ---

@app.post("/code_generation")
async def generate_code(request: CodeGenerationRequest):
    """Handles code generation requests using Codex CLI."""
    logger.info(f"Processing code generation for project '{request.project_id}' using Codex CLI")

    try:
        process = subprocess.run(
            ["codex-cli", "--prompt", request.prompt_text, "--config", json.dumps(request.config_options)],
            capture_output=True,
            text=True,
            check=True
        )
        generated_code = process.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.error(f"Codex CLI execution failed: {e.stderr.strip()}")
        raise HTTPException(status_code=500, detail=f"Codex CLI execution error: {e.stderr.strip()}")

    return {"status": "success", "generated_code": generated_code}

@app.post("/api/forgeiq/mcp/optimize-strategy/{project_id}", 
          response_model=MCPStrategyApiResponse, 
          tags=["MCP Integration"],
          summary="Request build strategy optimization from the private Master Control Program.",
          dependencies=[Depends(get_api_key)])
async def mcp_optimize_strategy_endpoint(
    project_id: str, 
    request_data: MCPStrategyApiRequest,
    intel_stack_client: httpx.AsyncClient = Depends(get_private_intel_client) 
):
    """Handles build strategy optimization requests through MCP."""
    logger.info(f"API: Forwarding optimize build strategy request for project '{project_id}' to private MCP.")

    if not intel_stack_client:
        logger.error("Private Intelligence API client (for MCP) not initialized.")
        raise HTTPException(status_code=503, detail="MCP service client not available.")

    mcp_payload = {
        "project_id": project_id,
        "current_dag_snapshot": request_data.current_dag_snapshot,
        "optimization_goal": request_data.optimization_goal,
        "additional_context": request_data.additional_mcp_context
    }

    try:
        response = await intel_stack_client.post("/mcp/request-strategy", json=mcp_payload)
        response.raise_for_status()
        mcp_response_data = response.json()
        strategy_details = None
        if mcp_response_data.get("status") == "strategy_provided":
            strategy_details = MCPStrategyApiDetails(
                strategy_id=mcp_response_data.get("strategy_id"),
                new_dag_definition_raw=mcp_response_data.get("new_dag_definition"),
                directives=mcp_response_data.get("directives"),
                mcp_metadata=mcp_response_data.get("mcp_execution_details")
            )
        return MCPStrategyApiResponse(
            project_id=project_id,
            status=mcp_response_data.get("status", "MCP_RESPONSE_UNKNOWN"),
            message=mcp_response_data.get("message"),
            strategy_details=strategy_details
        )
    except httpx.HTTPStatusError as e:
        logger.error(f"MCP API Error: {e.response.status_code} - {e.response.text[:200]}")
        raise HTTPException(status_code=502, detail=f"Error communicating with MCP service: HTTP {e.response.status_code}")
    except Exception as e:
        logger.error(f"Error during MCP call for project '{project_id}': {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal error while calling MCP service: {str(e)}")

@app.post("/api/forgeiq/algorithms/apply", 
          response_model=ApplyAlgorithmResponse, 
          tags=["Proprietary Algorithms"],
          summary="Apply a named proprietary algorithm via the Private Intelligence Stack.",
          dependencies=[Depends(get_api_key)])
async def apply_proprietary_algorithm_endpoint(
    request_data: ApplyAlgorithmRequest,
    intel_stack_client: httpx.AsyncClient = Depends(get_private_intel_client)
):
    """Executes a proprietary algorithm on project data."""
    logger.info(f"API: Request to apply proprietary algorithm '{request_data.algorithm_id}' for project '{request_data.project_id}'.")

    if not intel_stack_client:
        raise HTTPException(status_code=503, detail="Private Intelligence service client not available.")

    try:
        response = await intel_stack_client.post("/invoke_proprietary_algorithm", json={
            "algorithm_id": request_data.algorithm_id,
            "project_id": request_data.project_id,
            "context_data": request_data.context_data
        })
        response.raise_for_status()
        private_response_data = response.json()
        return ApplyAlgorithmResponse(**private_response_data)
    except httpx.HTTPStatusError as e:
        logger.error(f"Algorithm API Error: {e.response.status_code} - {e.response.text[:200]}")
        raise HTTPException(status_code=502, detail=f"Error from Algorithm Service: HTTP {e.response.status_code}")
    except Exception as e:
        logger.error(f"Internal error calling Algorithm Service: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal error calling Private Algorithm Service: {str(e)}")

@app.post("/pipeline_generate")
async def generate_pipeline(request: PipelineGenerateRequest):
    """Handles pipeline DAG generation requests."""
    logger.info(f"Generating pipeline for project '{request.project_id}'")
    return {"status": "accepted", "request_id": request.request_id}

@app.post("/deploy_service")
async def deploy_service(request: DeploymentTriggerRequest):
    """Handles deployment trigger requests."""
    logger.info(f"Triggering deployment for '{request.service_name}' in project '{request.project_id}'")
    return {"status": "accepted", "request_id": request.request_id}

@app.get("/task_list")
async def get_tasks() -> TaskListResponse:
    """Returns a list of tasks available in the system."""
    tasks = [TaskDefinitionModel(task_name=k, command_details=v) for k, v in TASK_COMMANDS.items()]
    return TaskListResponse(tasks=tasks)

# ✅ Application Lifespan Management
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown events."""
    logger.info("🚀 Starting ForgeIQ Backend...")
    yield
    logger.info("🛑 Shutting down ForgeIQ Backend...")

# ✅ Attach lifespan to FastAPI
app.router.lifespan_context = lifespan
from fastapi import Security, HTTPException

def get_api_key(api_key: str = Security(...)):  # Replace `...` with actual validation logic
    """Validates API key for secured endpoints."""
    if api_key != "your-secure-api-key":  # ✅ Replace with your real validation logic
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key
