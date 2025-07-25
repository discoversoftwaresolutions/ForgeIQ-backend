# File: forgeiq-backend/main.py

import os
import json
import datetime
import uuid
import logging
import asyncio
import subprocess # Still needed for subprocess.CalledProcessError
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, HTTPException, Body, Query, Depends, Security, status, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from sqlalchemy.orm import Session # For DB dependency injection
from sqlalchemy import text # <--- ADDED: For SQLAlchemy 2.0 raw SQL compatibility

# === Configure Logging ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Local imports specific to ForgeIQ ===
from app.auth import get_api_key, get_private_intel_client # For dependency injection
from app.database import get_db, SessionLocal # For database sessions (SessionLocal is imported but not directly used in main.py)
from app.models import ForgeIQTask # For ForgeIQ's task model
from forgeiq_utils import get_forgeiq_redis_client, update_forgeiq_task_state_and_notify

# === Import ForgeIQ's internal Celery tasks ===
from tasks.build_tasks import (
    run_codex_generation_task,
    run_forgeiq_pipeline_task, # The main pipeline orchestrator task for ForgeIQ
    run_pipeline_generation_task,
    run_deployment_trigger_task
)

# === Import ForgeIQ's API Models ===
from .api_models import (
    CodeGenerationRequest,
    PipelineGenerateRequest,
    DeploymentTriggerRequest,
    # ForgeIQ's response models (might include SDKTaskStatusModel for public facing status)
    MCPStrategyApiRequest,
    MCPStrategyApiResponse,
    ApplyAlgorithmRequest,
    ApplyAlgorithmResponse,
    # Other models
    ForgeIQTaskStatusResponse, # New Pydantic model for /forgeiq/status
    TaskPayloadFromOrchestrator # NEW: Pydantic model for incoming /build
)

# === Internal ForgeIQ components (already async or managed by tasks) ===
# from .index import TASK_COMMANDS # Assumed to be loaded within tasks or specific handlers
# from .mcp import MCPProcessor # Assumed to be used internally by Orchestrator or tasks
# from .algorithm import AlgorithmExecutor # Assumed to be used internally by Orchestrator or tasks
from orchestrator import Orchestrator # The orchestrator class

# === FastAPI instance ===
app = FastAPI(
    title="ForgeIQ Backend",
    description="Agentic intelligence and orchestration for engineering pipelines.",
    version="1.0.0"
)
logger.info("✅ Initializing ForgeIQ Backend FastAPI app.")

# === Global Async Redis Client instance for ForgeIQ ===
_global_forgeiq_redis_aio_client = None

@app.on_event("startup")
async def startup_event():
    global _global_forgeiq_redis_aio_client
    _global_forgeiq_redis_aio_client = await get_forgeiq_redis_client()
    # You might want to run Base.metadata.create_all(bind=engine) for ForgeIQ's DB here for dev
    # from app.models import Base # Ensure Base is imported in app/models for ForgeIQ
    # Base.metadata.create_all(bind=app.state.database_engine) # Use app.state.database_engine if stored there
    logger.info("✅ ForgeIQ: Database and async Redis client connected.")

@app.on_event("shutdown")
async def shutdown_event():
    if _global_forgeiq_redis_aio_client:
        await _global_forgeiq_redis_aio_client.close()
        logger.info("❌ ForgeIQ: Redis client closed.")
    # Assuming DB engine disposal is handled by app.database or similar global cleanup
    # if app.state.database_engine:
    #     app.state.database_engine.dispose()
    #     logger.info("❌ ForgeIQ: Database engine disposed.")


# === Browser-accessible root route ===
@app.get("/")
def root():
    return {
        "message": "✅ ForgeIQ Backend is live.",
        "docs": "/docs",
        "status": "/status",
        "version": app.version
    }

# === Health/status check ===
@app.get("/status", tags=["System"])
async def api_status(db: Session = Depends(get_db)): # Added DB dependency for full health check
    try:
        await _global_forgeiq_redis_aio_client.ping() # Check Redis connectivity
        # --- FIX APPLIED HERE ---
        db.execute(text("SELECT 1")) # <--- UPDATED: Wrap raw SQL with text()
        return {
            "status": "online",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "version": app.version,
            "database": "connected",
            "redis": "connected"
        }
    except Exception as e:
        logger.error(f"ForgeIQ health check failed: {e}")
        raise HTTPException(status_code=500, detail={"status": "unhealthy", "details": str(e)})


# --- NEW: Primary Build Endpoint (Called by Autosoft Orchestrator) ---
# This accepts a TaskPayload from Autosoft Orchestrator and dispatches the main ForgeIQ pipeline task.
@app.post("/build", response_model=Dict[str, Any], status_code=status.HTTP_202_ACCEPTED)
async def trigger_forgeiq_build_pipeline(task_payload_from_orchestrator: TaskPayloadFromOrchestrator, db: Session = Depends(get_db)):
    forgeiq_task_id = str(uuid.uuid4()) # Generate a unique ID for this internal ForgeIQ task
    
    # Create the initial ForgeIQTask record in ForgeIQ's database
    try:
        new_forgeiq_task = ForgeIQTask(
            id=forgeiq_task_id,
            task_type="build_orchestration", # Or "pipeline_build"
            status="pending",
            progress=0,
            current_stage="Queued",
            payload=task_payload_from_orchestrator.dict(), # Store the incoming payload
            logs="ForgeIQ build task received and queued."
        )
        db.add(new_forgeiq_task)
        db.commit()
        db.refresh(new_forgeiq_task)

        # Dispatch the main ForgeIQ pipeline task to its Celery queue
        run_forgeiq_pipeline_task.delay(task_payload_from_orchestrator.dict(), forgeiq_task_id)

        logger.info(f"ForgeIQ build request received. Internal Task ID: {forgeiq_task_id}. Task created in DB and dispatched.")

        return {
            "status": "accepted",
            "message": "ForgeIQ pipeline started in background.",
            "forgeiq_task_id": forgeiq_task_id # Return ForgeIQ's internal task ID
        }
    except Exception as e:
        db.rollback()
        logger.exception(f"ForgeIQ: Error initiating build task for {forgeiq_task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate ForgeIQ build task: {e}")


# --- Offloaded Endpoints (now dispatch Celery tasks) ---

@app.post("/code_generation", response_model=Dict[str, Any], status_code=status.HTTP_202_ACCEPTED)
async def generate_code_endpoint(request: CodeGenerationRequest, db: Session = Depends(get_db)):
    forgeiq_task_id = str(uuid.uuid4())
    
    try:
        new_forgeiq_task = ForgeIQTask(
            id=forgeiq_task_id,
            task_type="code_generation",
            status="pending",
            progress=0,
            current_stage="Queued",
            payload=request.dict(),
            logs="Code generation task received and queued."
        )
        db.add(new_forgeiq_task)
        db.commit()
        db.refresh(new_forgeiq_task)

        run_codex_generation_task.delay(request.dict(), forgeiq_task_id)
        logger.info(f"Code generation request received. Task ID: {forgeiq_task_id}.")
        return {"status": "accepted", "forgeiq_task_id": forgeiq_task_id}
    except Exception as e:
        db.rollback()
        logger.exception(f"ForgeIQ: Error initiating code generation task {forgeiq_task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate code generation task: {e}")


@app.post("/pipeline_generate", response_model=Dict[str, Any], status_code=status.HTTP_202_ACCEPTED)
async def generate_pipeline_endpoint(request: PipelineGenerateRequest, db: Session = Depends(get_db)):
    forgeiq_task_id = str(uuid.uuid4())

    try:
        new_forgeiq_task = ForgeIQTask(
            id=forgeiq_task_id,
            task_type="pipeline_generation",
            status="pending",
            progress=0,
            current_stage="Queued",
            payload=request.dict(),
            logs="Pipeline generation task received and queued."
        )
        db.add(new_forgeiq_task)
        db.commit()
        db.refresh(new_forgeiq_task)

        run_pipeline_generation_task.delay(request.dict(), forgeiq_task_id)
        logger.info(f"Pipeline generation request received. Task ID: {forgeiq_task_id}.")
        return {"status": "accepted", "forgeiq_task_id": forgeiq_task_id}
    except Exception as e:
        db.rollback()
        logger.exception(f"ForgeIQ: Error initiating pipeline generation task {forgeiq_task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate pipeline generation task: {e}")


@app.post("/deploy_service", response_model=Dict[str, Any], status_code=status.HTTP_202_ACCEPTED)
async def deploy_service_endpoint(request: DeploymentTriggerRequest, db: Session = Depends(get_db)):
    forgeiq_task_id = str(uuid.uuid4())

    try:
        new_forgeiq_task = ForgeIQTask(
            id=forgeiq_task_id,
            task_type="deployment_trigger",
            status="pending",
            progress=0,
            current_stage="Queued",
            payload=request.dict(),
            logs="Deployment trigger task received and queued."
        )
        db.add(new_forgeiq_task)
        db.commit()
        db.refresh(new_forgeiq_task)

        run_deployment_trigger_task.delay(request.dict(), forgeiq_task_id)
        logger.info(f"Deployment request received. Task ID: {forgeiq_task_id}.")
        return {"status": "accepted", "forgeiq_task_id": forgeiq_task_id}
    except Exception as e:
        db.rollback()
        logger.exception(f"ForgeIQ: Error initiating deployment task {forgeiq_task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate deployment task: {e}")


# --- Existing Endpoints (remain largely unchanged, rely on external calls) ---
# Assuming these make calls to the 'private intel client' which has its own async handling.

@app.post("/api/forgeiq/mcp/optimize-strategy/{project_id}", response_model=MCPStrategyApiResponse)
async def mcp_optimize_strategy_endpoint(
    project_id: str,
    request_data: MCPStrategyApiRequest,
    intel_stack_client: httpx.AsyncClient = Depends(get_private_intel_client),
    api_key: str = Depends(get_api_key) # This dependency is used to satisfy the Security(api_key_scheme)
):
    # This endpoint remains synchronous from ForgeIQ's perspective as it awaits intel_stack_client
    # The actual heavy lifting is expected to happen asynchronously within the intel_stack_client's service.
    if not intel_stack_client:
        raise HTTPException(status_code=503, detail="MCP service client unavailable.")
    try:
        # Assuming the Orchestrator class internally handles retries and specific exceptions
        # as per our previous update to forgeiq/orchestrator.py
        orchestrator_instance = Orchestrator(forgeiq_sdk_client=intel_stack_client, message_router=None)
        mcp_response_data = await orchestrator_instance.request_mcp_strategy_optimization(
            project_id=project_id,
            current_dag=None # You would provide current_dag if needed for this endpoint
        )
        if not mcp_response_data:
            raise HTTPException(status_code=500, detail="MCP strategy optimization returned no data.")
        
        # Convert dict response from Orchestrator method to Pydantic model
        return MCPStrategyApiResponse(**mcp_response_data)
    except Exception as e: # Catch OrchestrationError if raised from orchestrator.py
        logger.error(f"ForgeIQ: Error in MCP optimize strategy endpoint: {e}")
        raise HTTPException(status_code=500, detail=f"ForgeIQ MCP exception: {str(e)}")


@app.post("/api/forgeiq/algorithms/apply", response_model=ApplyAlgorithmResponse)
async def apply_proprietary_algorithm_endpoint(
    request_data: ApplyAlgorithmRequest,
    intel_stack_client: httpx.AsyncClient = Depends(get_private_intel_client),
    api_key: str = Depends(get_api_key) # This dependency is used to satisfy the Security(api_key_scheme)
):
    # This endpoint also remains synchronous from ForgeIQ's perspective
    if not intel_stack_client:
        raise HTTPException(status_code=503, detail="Algorithm service client unavailable.")
    try:
        # Assuming the AlgorithmExecutor or a similar internal component uses the intel_stack_client
        # and has its own retry/error handling.
        # For direct call to SDK:
        response = await intel_stack_client.post("/invoke_proprietary_algorithm", json={
            "algorithm_id": request_data.algorithm_id,
            "project_id": request_data.project_id,
            "context_data": request_data.context_data
        })
        response.raise_for_status()
        return ApplyAlgorithmResponse(**response.json())
    except httpx.HTTPStatusError as e:
        logger.error(f"ForgeIQ: HTTP error applying algorithm: {e.response.status_code} - {e.response.text[:200]}")
        raise HTTPException(status_code=502, detail=f"Algorithm service error: {e.response.text[:200]}")
    except Exception as e:
        logger.error(f"ForgeIQ: Error applying proprietary algorithm: {e}")
        raise HTTPException(status_code=500, detail=f"Error calling private algorithm: {str(e)}")


@app.get("/task_list", response_model=TaskListResponse)
def get_tasks():
    # This endpoint remains synchronous and serves static data
    from .index import TASK_COMMANDS # Assuming TASK_COMMANDS is defined here
    tasks = [TaskDefinitionModel(task_name=k, command_details=v) for k, v in TASK_COMMANDS.items()]
    return TaskListResponse(tasks=tasks)


# --- NEW: Internal ForgeIQ Task Status Endpoints ---

@app.get("/forgeiq/status/{forgeiq_task_id}", response_model=ForgeIQTaskStatusResponse)
async def get_forgeiq_task_status_endpoint(forgeiq_task_id: str, db: Session = Depends(get_db)):
    task = db.query(ForgeIQTask).filter(ForgeIQTask.id == forgeiq_task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="ForgeIQ Task not found")

    # Convert SQLAlchemy model to Pydantic ForgeIQTaskStatusResponse for response
    return ForgeIQTaskStatusResponse(
        task_id=task.id,
        task_type=task.task_type,
        status=task.status,
        current_stage=task.current_stage,
        progress=task.progress,
        logs=task.logs if task.logs else "",
        output_data=task.output_data if task.output_data else {},
        details=task.details if task.details else {}
    )

@app.websocket("/ws/forgeiq/status/{forgeiq_task_id}")
async def websocket_forgeiq_status_endpoint(websocket: WebSocket, forgeiq_task_id: str):
    await websocket.accept()
    logger.info(f"ForgeIQ WebSocket client connected for task_id: {forgeiq_task_id}")

    r = _global_forgeiq_redis_aio_client # Use the globally initialized async Redis client for this service
    pubsub = r.pubsub()
    channel_name = f"forgeiq_task_updates:{forgeiq_task_id}" # Specific channel for ForgeIQ tasks
    await pubsub.subscribe(channel_name)

    try:
        # Frontend should fetch initial state via GET /forgeiq/status/{forgeiq_task_id}
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message["type"] == "message":
                data = json.loads(message["data"])
                await websocket.send_json(data)
            await asyncio.sleep(0.1) # Small sleep to prevent busy-waiting / CPU hogging
    except WebSocketDisconnect:
        logger.info(f"ForgeIQ WebSocket client disconnected from task_id: {forgeiq_task_id}")
    except Exception as e:
        logger.error(f"ForgeIQ WebSocket error for task_id {forgeiq_task_id}: {e}")
    finally:
        await pubsub.unsubscribe(channel_name)


# === Lifecycle events ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting ForgeIQ Backend...")
    # This is where you might run database table creation for dev:
    # from app.database import create_db_tables
    # create_db_tables()
    yield
    logger.info("🛑 Shutting down ForgeIQ Backend...")

app.router.lifespan_context = lifespan

# === Run Server for Local Dev ===
if __name__ == "__main__":
    import uvicorn
    # IMPORTANT: Ensure your FORGEIQ_DATABASE_URL, FORGEIQ_REDIS_URL,
    # and other environment variables are set before running.
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True) # Use a different port, e.g., 8002 Please update the file
