# ==========================================
# 📁 apps/forgeiq-backend/app/main.py
# ==========================================
import os
import json
import datetime
import uuid
import logging
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, HTTPException, Body, Query # Added Query
from starlette.responses import JSONResponse

# --- Observability Setup (same as before) ---
SERVICE_NAME = "ForgeIQ_Backend_Py"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format=f'%(asctime)s - %(levelname)s - [{SERVICE_NAME}] - %(name)s - %(message)s')
tracer = None
try:
    from opentelemetry import trace
    from core.observability.tracing import setup_tracing
    tracer = setup_tracing(SERVICE_NAME)
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
except ImportError:
    logging.getLogger(SERVICE_NAME).warning("ForgeIQ-Backend: Tracing or FastAPIInstrumentor setup failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus
from core.message_router import MessageRouter, MessageRouteNotFoundError, InvalidMessagePayloadError
from core.shared_memory import SharedMemoryStore
from core.build_graph import get_project_dag, PROJECT_GRAPH # <<< IMPORT FROM YOUR MODULE
from core.task_runner import TASK_COMMANDS # <<< IMPORT FROM YOUR MODULE
from core.build_system_config import get_project_build_config as get_core_project_build_config # <<< IMPORT NEW

from .api_models import (
    PipelineGenerateRequest, PipelineGenerateResponse,
    DeploymentTriggerRequest, DeploymentTriggerResponse,
    SDKDagExecutionStatusModel, SDKDeploymentStatusModel,
    ProjectConfigResponse, BuildGraphResponse, TaskListResponse, TaskDefinitionModel # <<< NEW MODELS
)

event_bus_instance = EventBus()
message_router_instance = MessageRouter(event_bus_instance)
shared_memory_instance = SharedMemoryStore()

app = FastAPI(
    title="ForgeIQ Backend API (Python)",
    description="API Gateway for the ForgeIQ Agentic Build System.",
    version="2.0.0-alpha"
)

if tracer: # Apply FastAPI OTel Instrumentation
    try: FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer.provider if tracer else None)
    except Exception as e_otel_fastapi: logger.error(f"Failed to instrument FastAPI: {e_otel_fastapi}")

# === Existing API Endpoints (Health, Pipelines, Deployments - keep as is from response #62) ===
@app.get("/api/health", tags=["Health"], summary="Health check for ForgeIQ Backend")
async def health_check():
    # ... (same as before) ...
    logger.info("API /api/health invoked")
    redis_status = "unknown"
    if event_bus_instance.redis_client:
        try:
            # PING is synchronous, run in thread for async context
            await asyncio.to_thread(event_bus_instance.redis_client.ping) 
            redis_status = "connected"
        except Exception:
            redis_status = "disconnected"
    return {
        "service_name": SERVICE_NAME, "status": "healthy",
        "timestamp": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
        "environment": os.getenv("APP_ENV", "development"),
        "redis_event_bus_status": redis_status
    }

@app.post("/api/forgeiq/pipelines/generate", response_model=PipelineGenerateResponse, status_code=202, tags=["Pipelines"])
async def generate_pipeline_from_prompt_endpoint(request_data: PipelineGenerateRequest):
    # ... (same as before, uses message_router_instance) ...
    logger.info(f"API /pipelines/generate: ReqID '{request_data.request_id}', Project '{request_data.project_id}'")
    router_payload = {
        "request_id": request_data.request_id, "user_prompt_text": request_data.user_prompt_data.prompt_text,
        "target_project_id": request_data.project_id, "additional_context": request_data.user_prompt_data.additional_context,
        "requested_by": f"APIClient/{SERVICE_NAME}"
    }
    try:
        dispatch_success = await message_router_instance.dispatch(logical_message_type="GeneratePipelineFromPrompt", payload=router_payload)
        if dispatch_success: return PipelineGenerateResponse(message="Pipeline generation request accepted.", request_id=str(request_data.request_id))
        else: raise HTTPException(status_code=503, detail="Failed to dispatch request to event bus.")
    except (MessageRouteNotFoundError, InvalidMessagePayloadError) as e: raise HTTPException(status_code=400, detail=str(e))
    except Exception as e: logger.error(f"API /pipelines/generate: Unexpected error for ReqID {request_data.request_id}: {e}", exc_info=True); raise HTTPException(status_code=500, detail="Internal server error.")

@app.get("/api/forgeiq/projects/{project_id}/dags/{dag_id}/status", response_model=SDKDagExecutionStatusModel, tags=["Pipelines"])
async def get_dag_status_api_endpoint(project_id: str, dag_id: str):
    # ... (same as before, uses shared_memory_instance) ...
    logger.info(f"API /dags/status: Project '{project_id}', DAG '{dag_id}'")
    if not shared_memory_instance.redis_client: raise HTTPException(status_code=503, detail="Shared memory service unavailable.")
    status_key = f"dag_execution:{dag_id}:status_summary"
    cached_status_data = await shared_memory_instance.get_value(status_key, expected_type=dict)
    if cached_status_data:
        try: return SDKDagExecutionStatusModel(**cached_status_data)
        except Exception as e: logger.error(f"API /dags/status: Invalid data for DAG {dag_id}: {e}. Data: {cached_status_data}"); raise HTTPException(status_code=500, detail="Invalid status data format.")
    else:
        logger.warn(f"API /dags/status: No status found for DAG '{dag_id}' (key: '{status_key}')")
        return SDKDagExecutionStatusModel(dag_id=dag_id, project_id=project_id, status="NOT_FOUND", message="DAG status not found.", started_at=datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc).isoformat(), task_statuses=[])

@app.post("/api/forgeiq/deployments/trigger", response_model=DeploymentTriggerResponse, status_code=202, tags=["Deployments"])
async def trigger_deployment_api_endpoint(request_data: DeploymentTriggerRequest):
    # ... (same as before, uses message_router_instance) ...
    logger.info(f"API /deployments/trigger: ReqID '{request_data.request_id}' for service {request_data.service_name}")
    router_payload = {
        "request_id": request_data.request_id, "project_id": request_data.project_id,
        "service_name": request_data.service_name, "commit_sha": request_data.commit_sha,
        "target_environment": request_data.target_environment, "triggered_by": request_data.triggered_by or f"APIClient/{SERVICE_NAME}"
    }
    try:
        dispatch_success = await message_router_instance.dispatch(logical_message_type="RequestServiceDeployment", payload=router_payload)
        if dispatch_success: return DeploymentTriggerResponse(message="Deployment request accepted.", request_id=request_data.request_id)
        else: raise HTTPException(status_code=503, detail="Failed to dispatch deployment request.")
    except (MessageRouteNotFoundError, InvalidMessagePayloadError) as e: raise HTTPException(status_code=400, detail=str(e))
    except Exception as e: logger.error(f"API /deployments/trigger: Unexpected error for ReqID {request_data.request_id}: {e}", exc_info=True); raise HTTPException(status_code=500, detail="Internal server error.")

@app.get("/api/forgeiq/projects/{project_id}/services/{service_name}/deployments/{deployment_request_id}/status", response_model=SDKDeploymentStatusModel, tags=["Deployments"])
async def get_deployment_status_api_endpoint(project_id: str, service_name: str, deployment_request_id: str):
    # ... (same as before, uses shared_memory_instance) ...
    logger.info(f"API /deployments/status: RequestID '{deployment_request_id}'")
    if not shared_memory_instance.redis_client: raise HTTPException(status_code=503, detail="Shared memory service unavailable.")
    status_key = f"deployment:{deployment_request_id}:status_summary"
    cached_status_data = await shared_memory_instance.get_value(status_key, expected_type=dict)
    if cached_status_data:
        try: return SDKDeploymentStatusModel(**cached_status_data)
        except Exception as e: logger.error(f"API /deployments/status: Invalid data for req {deployment_request_id}: {e}. Data: {cached_status_data}"); raise HTTPException(status_code=500, detail="Invalid status data format.")
    else:
        logger.warn(f"API /deployments/status: No status for req '{deployment_request_id}' (key: '{status_key}')")
        return SDKDeploymentStatusModel(request_id=deployment_request_id, project_id=project_id, service_name=service_name, status="NOT_FOUND", message="Deployment status not found.")


# === NEW Build System API Endpoints ===

@app.get("/api/forgeiq/projects/{project_id}/build-config", response_model=ProjectConfigResponse, tags=["Build System"])
async def get_project_config_endpoint(project_id: str):
    logger.info(f"API: Request for build configuration for project '{project_id}'")
    # Using the get_core_project_build_config from core.build_system_config
    config_data = get_core_project_build_config(project_id) 
    if config_data:
        return ProjectConfigResponse(project_id=project_id, configuration=config_data)
    raise HTTPException(status_code=404, detail=f"Build configuration not found for project '{project_id}'")

@app.get("/api/forgeiq/projects/{project_id}/build-graph", response_model=BuildGraphResponse, tags=["Build System"])
async def get_project_build_graph_endpoint(project_id: str):
    logger.info(f"API: Request for build graph for project '{project_id}'")
    # Using get_project_dag from your core.build_graph module
    # This function returns a List[str] which is a sequence of tasks.
    tasks_sequence = get_project_dag(project_id) # From core.build_graph
    if not tasks_sequence and project_id not in PROJECT_GRAPH: # Check if project exists in graph definition
         raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in build graph definitions.")
    return BuildGraphResponse(project_id=project_id, tasks_sequence=tasks_sequence)

@app.get("/api/forgeiq/tasks", response_model=TaskListResponse, tags=["Build System"])
async def list_tasks_endpoint(project_id: Optional[str] = Query(None)): # project_id is an optional query parameter
    logger.info(f"API: Request to list available tasks (project_id: {project_id if project_id else 'all'})")
    # Using TASK_COMMANDS from your core.task_runner module
    tasks_output: List[TaskDefinitionModel] = []

    # Your TASK_COMMANDS is a simple dict. If project_id is given, we might filter,
    # but TASK_COMMANDS is global. Your PROJECT_GRAPH in core.build_graph defines tasks per project.
    # Let's list all known tasks from TASK_COMMANDS for now.
    # A more advanced version could consolidate task definitions.
    for task_name, command_details in TASK_COMMANDS.items():
        tasks_output.append(TaskDefinitionModel(
            task_name=task_name,
            command_details=command_details,
            description=f"Executes {task_name}" # Placeholder description
        ))
    return TaskListResponse(tasks=tasks_output)

@app.get("/api/forgeiq/tasks/{task_name}", response_model=TaskDefinitionModel, tags=["Build System"])
async def get_task_details_endpoint(task_name: str):
    logger.info(f"API: Request for details of task '{task_name}'")
    if task_name in TASK_COMMANDS:
        return TaskDefinitionModel(
            task_name=task_name,
            command_details=TASK_COMMANDS[task_name],
            description=f"Details for task: {task_name}" # Placeholder
        )
    raise HTTPException(status_code=404, detail=f"Task '{task_name}' not defined in core task runner.")

# --- Error Handlers & Server Start (same as before) ---
# (FastAPI has default error handling, but you can add custom handlers if needed)
# @app.exception_handler(Exception)
# async def generic_exception_handler(request: Request, exc: Exception): ...

# Uvicorn startup handled by Dockerfile CMD
