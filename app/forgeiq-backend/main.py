import os
import json
import datetime
import uuid
import logging
import asyncio
import subprocess
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional, List, Set

from fastapi import FastAPI, HTTPException, Body, Query, Depends, Security, status, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy.orm import Session
from sqlalchemy import text

import httpx

# === Configure Logging ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Local imports specific to ForgeIQ ===
from app.auth import get_api_key, get_private_intel_client
from app.database import get_db, create_db_tables
from app.models import ForgeIQTask

# === Celery App and Utilities imports ===
from forgeiq_celery import celery_app
from forgeiq_utils import get_forgeiq_redis_client, update_forgeiq_task_state_and_notify

# === Import ForgeIQ's internal Celery tasks ===
from tasks.build_tasks import (
    run_codex_generation_task,
    run_forgeiq_pipeline_task,
    run_pipeline_generation_task,
    run_deployment_trigger_task
)

# === Import ForgeIQ's API Models ===
from .api_models import (
    UserPromptData,
    CodeGenerationRequest,
    PipelineGenerateRequest,
    DeploymentTriggerRequest,
    ApplyAlgorithmRequest,
    MCPStrategyApiRequest,
    MCPStrategyApiResponse,
    ProjectConfigResponse,
    BuildGraphNodeModel,
    BuildGraphResponse,
    ForgeIQTaskStatusResponse,
    TaskDefinitionModel,
    TaskListResponse,
    TaskPayloadFromOrchestrator,
    SDKMCPStrategyRequestContext,
    SDKMCPStrategyResponse,
    ApplyAlgorithmResponse
)

# === Internal ForgeIQ components ===
from app.orchestrator import Orchestrator


# === OpenTelemetry (optional) ===
_tracer_main = None
_trace_api_main = None
try:
    from opentelemetry import trace as otel_trace_api
    from opentelmetry.instrumentation.fastapi import FastAPIInstrumentor

    _tracer_main = otel_trace_api.get_tracer("ForgeIQ Backend", "1.0.0")
    _trace_api_main = otel_trace_api
    class NoOpSpan:
        def __enter__(self): return self
        def __exit__(self, et, ev, tb): pass
        def set_attribute(self, k, v): pass
        def record_exception(self, e, a=None): pass
        def set_status(self, s): pass
        def end(self): pass
    _trace_api_main.NoOpContextManager = NoOpSpan
except ImportError:
    logger.info("ForgeIQ Backend: OpenTelemetry not available.")
    class _NoOpTraceAPI:
        class Status:
            def __init__(self, code, description=None): pass
        class StatusCode:
            OK = "OK"
            ERROR = "ERROR"
        def get_current_span_context(self): return None
        def Status(self, code, description=None):
            class NoOpStatus: pass
            return NoOpStatus()
        def NoOpContextManager(self):
            class NoOpCM:
                def __enter__(self): return None
                def __exit__(self, exc_type, exc_val, exc_tb): pass
            return NoOpCM()
    _trace_api_main = _NoOpTraceAPI()


# === FastAPI instance ===
app = FastAPI(
    title="ForgeIQ Backend",
    description="Agentic intelligence and orchestration for engineering pipelines.",
    version="1.0.0"
)
logger.info("✅ Initializing ForgeIQ Backend FastAPI app.")


# === CORS Middleware ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://auto-soft-front-io6tzy2gj-allenfounders-projects.vercel.app",  # Vercel frontend
        "http://localhost:3000",  # Local frontend
        "http://localhost:8000",  # Local backend
        "https://autosoft-deployment-repo-production.up.railway.app",  # Production backend
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)    
    # === Connection Manager for WebSockets ===
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.pubsub_task = None
        self.redis_client = None

    async def connect(self, websocket: WebSocket):
        try:
            await websocket.accept()
            self.active_connections.add(websocket)
            logger.info(f"WebSocket connected. Total active connections: {len(self.active_connections)}")
        except Exception as e:
            logger.error(f"Failed to accept WebSocket connection from {websocket.client.host}:{websocket.client.port}. Error: {e}", exc_info=True)
            if not isinstance(e, WebSocketDisconnect):
                raise

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket disconnected. Total active connections: {len(self.active_connections)}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except WebSocketDisconnect:
            logger.warning(f"Failed to send message to disconnected WebSocket {websocket}. Removing.")
            self.disconnect(websocket)
        except Exception as e:
            logger.error(f"Error sending message to WebSocket {websocket}: {e}")
            self.disconnect(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        disconnected_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except WebSocketDisconnect:
                disconnected_connections.append(connection)
            except Exception as e:
                logger.error(f"Error broadcasting message to WebSocket {connection}: {e}")
                disconnected_connections.append(connection)

        for connection in disconnected_connections:
            self.active_connections.remove(connection)
        logger.debug(f"Broadcasted message. Remaining active connections: {len(self.active_connections)}")

    async def start_pubsub_listener(self, redis_client):
        """Starts a background task to listen to Redis Pub/Sub."""
        self.redis_client = redis_client
        pubsub = self.redis_client.pubsub()
        await pubsub.subscribe("forgeiq_task_updates")

        logger.info("Started Redis Pub/Sub listener for channel 'forgeiq_task_updates'.")
        while True:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message":
                    data = json.loads(message["data"])
                    logger.debug(f"Received Pub/Sub message: {data}")
                    await self.broadcast(data)
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                logger.info("Pub/Sub listener task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in Pub/Sub listener: {e}", exc_info=True)
                await asyncio.sleep(1.0)

manager = ConnectionManager() # Global instance of ConnectionManager

# === Global Async Redis Client instance for ForgeIQ ===
_global_forgeiq_redis_aio_client = None

@app.on_event("startup")
async def startup_event():
    global _global_forgeiq_redis_aio_client
    _global_forgeiq_redis_aio_client = await get_forgeiq_redis_client()
    logger.info("✅ ForgeIQ: Database and async Redis client connected.")

    create_db_tables()
    logger.info("✅ ForgeIQ: Database tables ensured.")

    manager.pubsub_task = asyncio.create_task(manager.start_pubsub_listener(_global_forgeiq_redis_aio_client))
    logger.info("✅ ForgeIQ: WebSocket Pub/Sub listener started.")


@app.on_event("shutdown")
async def shutdown_event():
    if manager.pubsub_task:
        manager.pubsub_task.cancel()
        try:
            await manager.pubsub_task
        except asyncio.CancelledError:
            pass
        logger.info("❌ ForgeIQ: WebSocket Pub/Sub listener stopped.")
    if _global_forgeiq_redis_aio_client:
        await _global_forgeiq_redis_aio_client.close()
        logger.info("❌ ForgeIQ: Redis client closed.")


# === Browser-accessible root route ===
@app.get("/")
def root():
    # MODIFIED: Removed 'localhost' fallbacks.
    # These environment variables MUST now be set on Railway for this service.
    forgeiq_base_url = os.getenv("FORGEIQ_BASE_URL")
    orchestrator_base_url = os.getenv("ORCHESTRATOR_BASE_URL")

    if not forgeiq_base_url:
        logger.error("FORGEIQ_BASE_URL environment variable is NOT set. Root endpoint will return incomplete data.")
        raise ValueError("FORGEIQ_BASE_URL environment variable not set.")
    if not orchestrator_base_url:
        logger.error("ORCHESTRATOR_BASE_URL environment variable is NOT set. Root endpoint will return incomplete data.")
        raise ValueError("ORCHESTRATOR_BASE_URL environment variable not set.")

    return {
        "message": "✅ ForgeIQ Backend is live.",
        "docs": "/docs",
        "status": "/status",
        "version": app.version,
        "forgeiq": forgeiq_base_url,
        "orchestrator": orchestrator_base_url,
    }

# === Health/status check ===
@app.get("/status", tags=["System"])
async def api_status(db: Session = Depends(get_db)):
    try:
        await _global_forgeiq_redis_aio_client.ping()
        db.execute(text("SELECT 1"))
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


# --- Primary Build Endpoint (Called by Autosoft Orchestrator) ---
@app.post("/build", response_model=Dict[str, Any], status_code=status.HTTP_202_ACCEPTED)
async def trigger_forgeiq_build_pipeline(task_payload_from_orchestrator: TaskPayloadFromOrchestrator, db: Session = Depends(get_db)):
    forgeiq_task_id = str(uuid.uuid4())
    
    try:
        new_forgeiq_task = ForgeIQTask(
            id=forgeiq_task_id,
            task_type="build_orchestration",
            status="pending",
            progress=0,
            current_stage="Queued",
            payload=task_payload_from_orchestrator.dict(),
            logs="ForgeIQ build task received and queued."
        )
        db.add(new_forgeiq_task)
        db.commit()
        db.refresh(new_forgeiq_task)

        run_forgeiq_pipeline_task.delay(task_payload_from_orchestrator.dict(), forgeiq_task_id)

        logger.info(f"ForgeIQ build request received. Internal Task ID: {forgeiq_task_id}. Task created in DB and dispatched.")

        return {
            "status": "accepted",
            "message": "ForgeIQ pipeline started in background.",
            "forgeiq_task_id": forgeiq_task_id
        }
    except Exception as e:
        db.rollback()
        logger.exception(f"ForgeIQ: Error initiating build task for {forgeiq_task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate ForgeIQ build task: {e}")


# --- MODIFIED: /gateway Endpoint (asynchronous dispatch) ---
@app.post("/gateway", status_code=status.HTTP_202_ACCEPTED)
async def handle_gateway_request(request_data: UserPromptData, db: Session = Depends(get_db)):
    """
    Handles general AI requests from the frontend asynchronously.
    Dispatches a Celery task and immediately returns a task ID.
    The frontend should track progress via a central WebSocket listener.
    """
    forgeiq_task_id = str(uuid.uuid4())
    
    try:
        new_forgeiq_task = ForgeIQTask(
            id=forgeiq_task_id,
            task_type="llm_chat_response",
            status="pending",
            progress=0,
            current_stage="Queued for LLM processing",
            payload=request_data.dict(),
            logs="LLM request received and queued for processing."
        )
        db.add(new_forgeiq_task)
        db.commit()
        db.refresh(new_forgeiq_task)

        logger.info(f"Gateway: Dispatching LLM request for task ID: {forgeiq_task_id}. Prompt: '{request_data.prompt[:50]}'")

        run_codex_generation_task.delay(request_data.dict(), forgeiq_task_id)
        
        return JSONResponse({
            "status": "accepted",
            "message": "LLM processing started in background. Updates will be sent via WebSocket.",
            "task_id": forgeiq_task_id
        })

    except Exception as e:
        db.rollback()
        logger.exception(f"ForgeIQ Gateway: Error initiating LLM task for prompt: '{request_data.prompt}': {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate LLM task: {e}")


# --- MODIFIED: Generic WebSocket Endpoint ---
@app.websocket("/ws/tasks/updates")
async def websocket_task_updates(websocket: WebSocket):
    try:
        await manager.connect(websocket)
        while True:
            message = await websocket.receive_text()
            logger.debug(f"Received message from WebSocket client: {message}")
    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected from /ws/tasks/updates.")
    except Exception as e:
        logger.error(f"Unhandled error in /ws/tasks/updates WebSocket: {e}", exc_info=True)
    finally:
        manager.disconnect(websocket)


# --- Offloaded Endpoints (dispatch Celery tasks) ---

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
        orchestrator_instance = Orchestrator(forgeiq_sdk_client=intel_stack_client, message_router=None)
        mcp_response_data = await orchestrator_instance.request_mcp_strategy_optimization(
            project_id=project_id,
            current_dag=None
        )
        if not mcp_response_data:
            raise HTTPException(status_code=500, detail="MCP strategy optimization returned no data.")
        
        return MCPStrategyApiResponse(**mcp_response_data)
    except Exception as e:
        logger.error(f"ForgeIQ: Error in MCP optimize strategy endpoint: {e}")
        raise HTTPException(status_code=500, detail=f"ForgeIQ MCP exception: {str(e)}")


@app.post("/api/forgeiq/algorithms/apply", response_model=ApplyAlgorithmResponse)
async def apply_proprietary_algorithm_endpoint(
    request_data: ApplyAlgorithmRequest,
    intel_stack_client: httpx.AsyncClient = Depends(get_private_intel_client),
    api_key: str = Depends(get_api_key)
):
    if not intel_stack_client:
        raise HTTPException(status_code=503, detail="Algorithm service client unavailable.")
    try:
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
    from .index import TASK_COMMANDS # Assuming TASK_COMMANDS is defined here
    tasks = [TaskDefinitionModel(task_name=k, command_details=v) for k, v in TASK_COMMANDS.items()]
    return TaskListResponse(tasks=tasks)


# --- Internal ForgeIQ Task Status REST Endpoint (for polling if WS fails) ---
@app.get("/forgeiq/status/{forgeiq_task_id}", response_model=ForgeIQTaskStatusResponse)
async def get_forgeiq_task_status_endpoint(forgeiq_task_id: str, db: Session = Depends(get_db)):
    task = db.query(ForgeIQTask).filter(ForgeIQTask.id == forgeiq_task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="ForgeIQ Task not found")

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


# === Lifecycle events ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting ForgeIQ Backend...")
    create_db_tables()
    logger.info("✅ ForgeIQ: Database tables ensured.")
    yield
    logger.info("🛑 Shutting down ForgeIQ Backend...")

app.router.lifespan_context = lifespan

# === Run Server for Local Dev ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
