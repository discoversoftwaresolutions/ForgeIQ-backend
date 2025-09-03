import os
import json
import datetime
import uuid
import logging
import asyncio
import subprocess
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional, List, Set, Tuple

from fastapi import FastAPI, HTTPException, Body, Query, Depends, Security, status, WebSocket, WebSocketDisconnect, APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sqlalchemy.orm import Session
from sqlalchemy import text

import httpx
import stripe
import hmac, hashlib

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
from tasks.build_tasks import run_codex_generation_task
from tasks.build_tasks import run_forgeiq_pipeline_task
from tasks.build_tasks import run_pipeline_generation_task
from tasks.build_tasks import run_deployment_trigger_task

# === Import ForgeIQ's API Models ===
from .api_models import UserPromptData
from .api_models import CodeGenerationRequest
from .api_models import PipelineGenerateRequest
from .api_models import DeploymentTriggerRequest
from .api_models import ApplyAlgorithmRequest
from .api_models import MCPStrategyApiRequest
from .api_models import MCPStrategyApiResponse
from .api_models import ProjectConfigResponse
from .api_models import BuildGraphNodeModel
from .api_models import BuildGraphResponse
from .api_models import ForgeIQTaskStatusResponse
from .api_models import TaskDefinitionModel
from .api_models import TaskListResponse
from .api_models import TaskPayloadFromOrchestrator
from .api_models import SDKMCPStrategyRequestContext
from .api_models import SDKMCPStrategyResponse
from .api_models import ApplyAlgorithmResponse
from .api_models import DemoRequestPayload  # kept for compatibility

# === Internal ForgeIQ components ===
from app.orchestrator import Orchestrator

# === OpenTelemetry (optional) ===
_tracer_main = None
_trace_api_main = None
try:
    from opentelemetry import trace as otel_trace_api
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # fixed typo

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

# === CORS ===
ALLOWED_ORIGINS = [
    "https://forgeiq-production.up.railway.app",          # Frontend (prod)
    "http://localhost:3000",
    "http://localhost:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=86400,
)

# === Soketi / Pusher-compatible configuration ===
SOKETI_APP_ID = os.getenv("SOKETI_APP_ID", "forgeiq")
SOKETI_KEY = os.getenv("SOKETI_KEY", "local")                 # public key used by client + auth
SOKETI_SECRET = os.getenv("SOKETI_SECRET", "secret")          # keep secret on server
SOKETI_HOST = os.getenv("SOKETI_HOST", "soketi-forgeiq-production.up.railway.app")
SOKETI_PORT = int(os.getenv("SOKETI_PORT", "443"))
SOKETI_TLS = os.getenv("SOKETI_TLS", "true").lower() == "true"
PUSHER_CLUSTER = os.getenv("PUSHER_CLUSTER", "mt1")           # client-side required field

# === LLM provider defaults (for transparency/config UI) ===
LLM_PROVIDER_PRIORITY = os.getenv("LLM_PROVIDER_PRIORITY", "openai,gemini,codex")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-pro-latest")

def _pusher_auth_signature(message: str) -> str:
    """Return hex HMAC-SHA256 signature for auth payload."""
    mac = hmac.new(SOKETI_SECRET.encode("utf-8"), msg=message.encode("utf-8"), digestmod=hashlib.sha256)
    return mac.hexdigest()

def _emit_to_soketi(channel: str, event: str, payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Trigger an event to Soketi using its HTTP API (Pusher REST-compatible).
    """
    import time, urllib.parse

    scheme = "https" if SOKETI_TLS else "http"
    host = f"{scheme}://{SOKETI_HOST}:{SOKETI_PORT}"
    path = f"/apps/{SOKETI_APP_ID}/events"
    method = "POST"
    body = json.dumps({
        "name": event,
        "channels": [channel],
        "data": json.dumps(payload),
    })

    auth_timestamp = str(int(time.time()))
    auth_version = "1.0"
    query_params = {
        "auth_key": SOKETI_KEY,
        "auth_timestamp": auth_timestamp,
        "auth_version": auth_version,
        "body_md5": hashlib.md5(body.encode("utf-8")).hexdigest(),
    }
    query_str = "&".join(f"{k}={urllib.parse.quote(v)}" for k, v in sorted(query_params.items()))
    string_to_sign = "\n".join([method, path, query_str])
    auth_signature = _pusher_auth_signature(string_to_sign)
    url = f"{host}{path}?{query_str}&auth_signature={auth_signature}"

    try:
        resp = httpx.post(url, content=body, headers={"Content-Type": "application/json"}, timeout=5.0)
        if 200 <= resp.status_code < 300:
            return True, None
        return False, f"{resp.status_code}: {resp.text}"
    except Exception as e:
        return False, str(e)

def emit_task_update(payload: Dict[str, Any]) -> None:
    """
    Generic emitter used from Redis pub/sub listener to forward updates to Soketi.
    """
    event = payload.get("event") or "TaskUpdated"
    ok, err = _emit_to_soketi("private-forgeiq", event, payload)
    if not ok:
        logger.warning(f"Soketi emit failed: {err}")

# === Connection Manager for WebSockets (native WS kept for compatibility) ===
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
            client = getattr(websocket, "client", None)
            host = getattr(client, "host", "?") if client else "?"
            port = getattr(client, "port", "?") if client else "?"
            logger.error(f"Failed to accept WebSocket connection from {host}:{port}. Error: {e}", exc_info=True)
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
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except WebSocketDisconnect:
                disconnected_connections.append(connection)
            except Exception as e:
                logger.error(f"Error broadcasting message to WebSocket {connection}: {e}")
                disconnected_connections.append(connection)

        for connection in disconnected_connections:
            self.active_connections.discard(connection)
        logger.debug(f"Broadcasted message. Remaining active connections: {len(self.active_connections)}")

    async def start_pubsub_listener(self, redis_client):
        """Starts a background task to listen to Redis Pub/Sub and mirror to Soketi."""
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
                    # Native WS broadcast
                    await self.broadcast(data)
                    # Forward to Soketi (Pusher) as well
                    try:
                        emit_task_update(data)
                    except Exception as e:
                        logger.warning(f"Failed to emit to Soketi: {e}")
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                logger.info("Pub/Sub listener task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in Pub/Sub listener: {e}", exc_info=True)
                await asyncio.sleep(1.0)

manager = ConnectionManager()  # Global instance

# === FastAPI events ===
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

# === Public config for frontend (LLM defaults + Soketi public info) ===
@app.get("/config", tags=["System"])
def public_config():
    """
    Returns non-sensitive runtime configuration that the frontend can safely read.
    """
    return {
        "llm": {
            "provider_priority": LLM_PROVIDER_PRIORITY,
            "openai_model": OPENAI_MODEL,
            "gemini_model": GEMINI_MODEL,
        },
        "realtime": {
            "wsHost": SOKETI_HOST,
            "wsPort": SOKETI_PORT,
            "forceTLS": SOKETI_TLS,
            "cluster": PUSHER_CLUSTER,
            "appId": SOKETI_APP_ID,
            "publicKey": SOKETI_KEY,     # This is the client key; safe to expose
        }
    }

# === Pusher/Soketi private & presence auth ===
@app.post("/api/broadcasting/auth")
async def pusher_auth(request: Request):
    """
    Pusher-compatible auth endpoint. Expects socket_id and channel_name.
    Optional presence: user_id, user_info (JSON).
    """
    # Support both form and JSON
    try:
        form = await request.form()
        socket_id = form.get("socket_id")
        channel_name = form.get("channel_name")
        user_id = form.get("user_id")
        user_info_raw = form.get("user_info")
    except Exception:
        # Fall back to JSON if not form
        body = await request.json()
        socket_id = body.get("socket_id")
        channel_name = body.get("channel_name")
        user_id = body.get("user_id")
        user_info_raw = body.get("user_info")

    if not socket_id or not channel_name:
        raise HTTPException(status_code=400, detail="socket_id and channel_name are required")

    # Presence channel data
    channel_data = None
    if channel_name.startswith("presence-"):
        if not user_id:
            user_id = f"anon-{uuid.uuid4().hex[:8]}"
        try:
            user_info = json.loads(user_info_raw) if user_info_raw else {}
        except Exception:
            user_info = {}
        channel_data = json.dumps({"user_id": user_id, "user_info": user_info})

    # Build signature
    if channel_data:
        string_to_sign = f"{socket_id}:{channel_name}:{channel_data}"
    else:
        string_to_sign = f"{socket_id}:{channel_name}"

    signature = _pusher_auth_signature(string_to_sign)
    auth = f"{SOKETI_KEY}:{signature}"
    resp = {"auth": auth}
    if channel_data:
        resp["channel_data"] = channel_data
    return JSONResponse(resp)

# --- Primary Build Endpoint (Called by Orchestrator) ---
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

        # NOTE: body is forwarded to the Celery task; it may include providers[] / llm_provider
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
        raise HTTPException(status_code=500, detail=f"Failed to initiate build task: {e}")

@app.post("/gateway", status_code=status.HTTP_202_ACCEPTED)
async def handle_gateway_request(request_data: UserPromptData, db: Session = Depends(get_db)):
    """
    Handles general AI requests asynchronously.
    Accepts context_data.config_options.{ providers: [..], llm_provider: "..." } for LLM routing.
    """
    if not request_data.prompt or len(request_data.prompt.strip()) == 0:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    forgeiq_task_id = str(uuid.uuid4())
    session_id = request_data.session_id or str(uuid.uuid4())
    request_payload = request_data.dict()
    request_payload["session_id"] = session_id

    try:
        new_forgeiq_task = ForgeIQTask(
            id=forgeiq_task_id,
            task_type="llm_chat_response",
            status="pending",
            progress=0,
            current_stage="Queued for LLM processing",
            payload=request_payload,
            logs="LLM request received and queued for processing."
        )
        db.add(new_forgeiq_task)
        db.commit()
        db.refresh(new_forgeiq_task)

        logger.info(f"Gateway: Dispatching LLM request for task ID: {forgeiq_task_id}. Prompt: '{request_data.prompt[:50]}'")

        run_codex_generation_task.delay(request_payload, forgeiq_task_id)

        return JSONResponse({
            "status": "accepted",
            "message": "LLM processing started in background. Updates will be sent via WebSocket.",
            "task_id": forgeiq_task_id,
            "session_id": session_id,
            "task_type": "llm_chat_response",
            "timestamp": datetime.datetime.utcnow().isoformat()
        })

    except Exception as e:
        db.rollback()
        logger.exception(f"ForgeIQ Gateway: Error initiating LLM task for prompt: '{request_data.prompt}': {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate LLM task: {e}")

# --- Generic WebSocket Endpoint (native WS; Soketi remains primary) ---
@app.websocket("/ws/tasks/updates")
async def websocket_task_updates(websocket: WebSocket):
    try:
        await manager.connect(websocket)
        while True:
            _ = await websocket.receive_text()  # keepalive from client
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

# === Dashboard summary endpoints ===
@app.get("/api/forgeiq/system/overall-summary", tags=["Dashboard"])
async def overall_summary(db: Session = Depends(get_db)):
    # Heuristic aggregation from tasks in last 24h
    failed = 0
    running = 0
    completed = 0
    try:
        rows = db.execute(text("""
            SELECT status, COUNT(*) as c
            FROM forgeiq_tasks
            WHERE created_at >= NOW() - INTERVAL '24 HOURS'
            GROUP BY status
        """)).fetchall()
        for status_val, c in rows:
            st = (status_val or "").lower()
            cnt = int(c or 0)
            if "fail" in st or "error" in st: failed += cnt
            elif any(x in st for x in ["pending", "running", "progress", "processing"]): running += cnt
            elif any(x in st for x in ["complete", "success"]): completed += cnt
    except Exception:
        pass

    if failed == 0:
        health = "Operational"
    elif failed <= 2:
        health = "Minor Issues"
    else:
        health = "Degraded"

    projects_count = 0
    try:
        rows = db.execute(text("""
            SELECT DISTINCT COALESCE((payload->>'project')::text, (details->>'project')::text) as project
            FROM forgeiq_tasks
            WHERE created_at >= NOW() - INTERVAL '7 DAYS'
        """)).fetchall()
        projects_count = len([1 for r in rows if r and r[0]])
    except Exception:
        pass

    return {
        "active_projects_count": projects_count,
        "total_agents_defined": 12,               # TODO: replace with real agents registry
        "agents_online_count": 9,                 # TODO: replace with real heartbeats
        "critical_alerts_count": failed,
        "system_health_status": health
    }

@app.get("/api/forgeiq/projects/summary", tags=["Dashboard"])
async def projects_summary(limit: int = Query(3, ge=1, le=12), db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, status, created_at, payload
        FROM forgeiq_tasks
        WHERE task_type IN ('build_orchestration','pipeline_generation')
        ORDER BY created_at DESC
        LIMIT :limit
    """), {"limit": limit}).mappings().all()
    resp = []
    for r in rows:
        status = str(r["status"] or "").upper() or "N/A"
        ts = r.get("created_at")
        payload = r.get("payload") or {}
        if isinstance(payload, str):
            try: payload = json.loads(payload)
            except Exception: payload = {}
        name = payload.get("project") or payload.get("project_id") or f"Task {r['id'][:8]}"
        resp.append({
            "name": name,
            "last_build_status": status,
            "last_build_timestamp": (ts.isoformat() + "Z") if hasattr(ts, "isoformat") else datetime.datetime.utcnow().isoformat() + "Z",
            "repo_url": payload.get("repo_url"),
        })
    return resp

@app.get("/api/forgeiq/pipelines/recent-summary", tags=["Dashboard"])
async def pipelines_recent_summary(limit: int = Query(3, ge=1, le=12), db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, status, created_at, payload
        FROM forgeiq_tasks
        WHERE task_type IN ('build_orchestration','pipeline_generation')
        ORDER BY created_at DESC
        LIMIT :limit
    """), {"limit": limit}).mappings().all()
    items = []
    for r in rows:
        ts = r.get("created_at")
        payload = r.get("payload") or {}
        if isinstance(payload, str):
            try: payload = json.loads(payload)
            except Exception: payload = {}
        items.append({
            "dag_id": f"dag_{r['id'][:8]}",
            "project_id": payload.get("project") or payload.get("project_id") or "unknown",
            "status": str(r["status"] or "").upper() or "RUNNING",
            "started_at": (ts.isoformat() + "Z") if hasattr(ts, "isoformat") else datetime.datetime.utcnow().isoformat() + "Z",
            "trigger": payload.get("trigger") or "Scheduled"
        })
    return items

@app.get("/api/forgeiq/deployments/recent-summary", tags=["Dashboard"])
async def deployments_recent_summary(limit: int = Query(3, ge=1, le=12), db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, status, created_at, payload, details
        FROM forgeiq_tasks
        WHERE task_type = 'deployment_trigger'
        ORDER BY created_at DESC
        LIMIT :limit
    """), {"limit": limit}).mappings().all()
    items = []
    for r in rows:
        ts = r.get("created_at")
        p = r.get("payload") or {}
        d = r.get("details") or {}
        if isinstance(p, str):
            try: p = json.loads(p)
            except Exception: p = {}
        if isinstance(d, str):
            try: d = json.loads(d)
            except Exception: d = {}
        items.append({
            "deployment_id": f"depl_{r['id'][:8]}",
            "service_name": p.get("service_name") or d.get("service") or "unknown",
            "target_environment": p.get("environment") or d.get("env") or "staging",
            "commit_sha": (p.get("commit_sha") or d.get("commit") or "")[:7],
            "status": str(r["status"] or "").upper() or "IN_PROGRESS",
            "completed_at": (ts.isoformat() + "Z") if hasattr(ts, "isoformat") else datetime.datetime.utcnow().isoformat() + "Z",
        })
    return items

# --- Live pipeline run (real; replaces demo) ---
@app.post("/pipelines/run", response_model=Dict[str, Any], status_code=status.HTTP_202_ACCEPTED)
async def run_pipeline_live(request: PipelineGenerateRequest, db: Session = Depends(get_db)):
    """
    Kicks off the real ForgeIQ pipeline using your Celery task.

    NOTE: The request body is passed straight through to the worker and may include
    `providers: [..]` (preferred) and/or `llm_provider: "..."` to control LLM routing.
    """
    forgeiq_task_id = str(uuid.uuid4())

    try:
        new_task = ForgeIQTask(
            id=forgeiq_task_id,
            task_type="pipeline_generation",
            status="pending",
            progress=0,
            current_stage="Queued",
            payload=request.dict(),     # preserves providers[] / llm_provider if supplied
            logs="Pipeline run requested and queued."
        )
        db.add(new_task)
        db.commit()
        db.refresh(new_task)

        # Dispatch Celery task; worker publishes progress to Redis "forgeiq_task_updates"
        run_forgeiq_pipeline_task.delay(request.dict(), forgeiq_task_id)

        logger.info(f"Pipeline run request accepted. Task ID: {forgeiq_task_id}")
        return {"status": "accepted", "forgeiq_task_id": forgeiq_task_id}
    except Exception as e:
        db.rollback()
        logger.exception(f"Error initiating live pipeline: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initiate pipeline: {e}")

# === Billing (Stripe) ===
billing_router = APIRouter(tags=["Billing"])

class CreateCheckoutSessionRequest(BaseModel):
    priceId: str

class CreateCheckoutSessionResponse(BaseModel):
    id: str

@billing_router.post("/api/create-checkout-session", response_model=CreateCheckoutSessionResponse)
async def create_checkout_session(request_data: CreateCheckoutSessionRequest):
    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
    frontend_success = os.getenv("FRONTEND_SUCCESS_URL", "https://forgeiq-production.up.railway.app/success?session_id={CHECKOUT_SESSION_ID}")
    frontend_cancel  = os.getenv("FRONTEND_CANCEL_URL", "https://forgeiq-production.up.railway.app/cancel")

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": request_data.priceId, "quantity": 1}],
            mode="subscription",
            success_url=frontend_success,
            cancel_url=frontend_cancel,
        )
        return CreateCheckoutSessionResponse(id=checkout_session.id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@billing_router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except ValueError as e:
        return JSONResponse(content={"detail": str(e)}, status_code=400)
    except stripe.error.SignatureVerificationError as e:
        return JSONResponse(content={"detail": str(e)}, status_code=400)

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        logger.info("Payment received for session: %s", session.get("id"))
    elif event['type'] == 'invoice.payment_succeeded':
        logger.info("Subscription payment successful!")
    return JSONResponse(content={"status": "success"}, status_code=200)

# === Lifecycle events ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting ForgeIQ Backend...")
    create_db_tables()
    logger.info("✅ ForgeIQ: Database tables ensured.")
    yield
    logger.info("🛑 Shutting down ForgeIQ Backend...")

app.router.lifespan_context = lifespan

# Include the billing router
app.include_router(billing_router)

# === Run Server for Local Dev ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
