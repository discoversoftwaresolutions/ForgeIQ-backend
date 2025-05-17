# ===================================================
# 📁 apps/forgeiq-backend/main.py (V0.2 Enhanced)
# ===================================================
import os
import json
import datetime
import uuid
import logging
import asyncio # For health check ping and other async operations
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, HTTPException, Body, Query, Depends
from starlette.responses import JSONResponse
from pydantic import BaseModel # For utility if needed, api_models has main ones

# --- Observability Setup ---
SERVICE_NAME = "ForgeIQ_Backend_Py"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format=f'%(asctime)s - %(levelname)s - [{SERVICE_NAME}] - %(name)s - %(message)s')
_tracer = None; _trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing
    _tracer = setup_tracing(SERVICE_NAME)
    _trace_api = otel_trace_api
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
except ImportError: logging.getLogger(SERVICE_NAME).warning("ForgeIQ-Backend: Tracing/FastAPIInstrumentor failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

from core.event_bus.redis_bus import EventBus
from core.message_router import MessageRouter, MessageRouteNotFoundError, InvalidMessagePayloadError
from core.shared_memory import SharedMemoryStore
from core.build_system_config import get_project_build_config as get_core_project_config, get_all_project_configs, PROJECT_CONFIGURATIONS
from core.build_graph import get_project_dag, PROJECT_GRAPH
from core.task_runner import TASK_COMMANDS

from .api_models import (
    PipelineGenerateRequest, PipelineGenerateResponse,
    DeploymentTriggerRequest, DeploymentTriggerResponse,
    SDKDagExecutionStatusModel, SDKDeploymentStatusModel,
    ProjectConfigResponse, BuildGraphResponse, TaskListResponse, TaskDefinitionModel
    # Add Pydantic models for list responses if needed, e.g., ProjectListResponse
)
# Import TypedDicts from interfaces for data consistency, though Pydantic models are used for API
from interfaces.types.agent import AgentRegistrationInfo 
from interfaces.types.events import SecurityScanResultEvent, GovernanceAlertEvent, SLAViolationEvent

# --- Initialize Core Services (Singleton pattern for simplicity in V0.1) ---
# In a larger app, use FastAPI's dependency injection for these.
event_bus_instance = EventBus() # Assumes REDIS_URL is set globally
message_router_instance = MessageRouter(event_bus_instance)
shared_memory_instance = SharedMemoryStore() # Assumes REDIS_URL and prefix are set globally

# --- FastAPI App Initialization ---
app = FastAPI(
    title="ForgeIQ Backend API (Python)",
    description="API Gateway for the ForgeIQ Agentic Build System.",
    version="2.0.1" # Incremented version
)

if _tracer: # Apply FastAPI OTel Instrumentation
    try: FastAPIInstrumentor.instrument_app(app, tracer_provider=_tracer.provider if _tracer else None) # type: ignore
    except Exception as e_otel_fastapi: logger.error(f"Failed to instrument FastAPI: {e_otel_fastapi}")

# --- Helper for Tracing within Endpoints ---
def _start_api_span(name: str, **attrs):
    if _tracer:
        span = _tracer.start_as_current_span(f"api.{name}")
        for k,v in attrs.items(): span.set_attribute(k,v)
        return span
    class NoOpSpanCM: # Context Manager
        def __enter__(self): return None
        def __exit__(self,et,ev,tb): pass
    return NoOpSpanCM()


# === API Endpoints ===

@app.get("/api/health", tags=["Health"], summary="Health check for ForgeIQ Backend")
async def health_check():
    with _start_api_span("health_check"):
        logger.info("API /api/health invoked")
        redis_ok = False
        if shared_memory_instance.redis_client: # Check via one of the Redis-dependent services
            try:
                # redis-py ping is synchronous, run in thread for async FastAPI endpoint
                await asyncio.to_thread(shared_memory_instance.redis_client.ping)
                redis_ok = True
            except Exception as e_redis:
                logger.error(f"Health check: Redis ping failed: {e_redis}")

        return {
            "service_name": SERVICE_NAME, "status": "healthy",
            "timestamp": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            "environment": os.getenv("APP_ENV", "development"),
            "redis_connection_status": "connected" if redis_ok else "disconnected_or_error"
        }

@app.post("/api/forgeiq/pipelines/generate", response_model=PipelineGenerateResponse, status_code=202, tags=["Pipelines"])
async def generate_pipeline_from_prompt_endpoint(request_data: PipelineGenerateRequest):
    with _start_api_span("generate_pipeline", project_id=request_data.project_id, request_id=request_data.request_id):
        logger.info(f"API /pipelines/generate: ReqID '{request_data.request_id}', Project '{request_data.project_id}'")
        router_payload = {
            "request_id": request_data.request_id, 
            "user_prompt_text": request_data.user_prompt_data.prompt_text,
            "target_project_id": request_data.project_id, 
            "additional_context": request_data.user_prompt_data.additional_context,
            "requested_by": f"APIClient/{SERVICE_NAME}"
        }
        try:
            dispatch_success = await message_router_instance.dispatch(
                logical_message_type="GeneratePipelineFromPrompt", payload=router_payload
            ) # dispatch is async
            if dispatch_success:
                return PipelineGenerateResponse(message="Pipeline generation request accepted.", request_id=request_data.request_id)
            else:
                logger.error(f"API /pipelines/generate: Dispatch returned False for ReqID {request_data.request_id}.")
                raise HTTPException(status_code=503, detail="Failed to dispatch pipeline generation request via event bus.")
        except (MessageRouteNotFoundError, InvalidMessagePayloadError) as e:
            logger.error(f"API /pipelines/generate: Dispatch error for ReqID {request_data.request_id}: {e}", exc_info=True)
            raise HTTPException(status_code=400, detail=f"Invalid request: {str(e)}")
        except Exception as e:
            logger.error(f"API /pipelines/generate: Unexpected error for ReqID {request_data.request_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error.")

@app.get("/api/forgeiq/projects/{project_id}/dags/{dag_id}/status", response_model=SDKDagExecutionStatusModel, tags=["Pipelines"])
async def get_dag_status_api_endpoint(project_id: str, dag_id: str):
    with _start_api_span("get_dag_status", project_id=project_id, dag_id=dag_id):
        logger.info(f"API /dags/status: Project '{project_id}', DAG '{dag_id}'")
        if not shared_memory_instance.redis_client:
            raise HTTPException(status_code=503, detail="Shared memory service unavailable.")

        status_key = f"dag_execution:{dag_id}:status_summary" # Key PlanAgent writes to
        cached_status_data = await shared_memory_instance.get_value(status_key, expected_type=dict)

        if cached_status_data:
            logger.debug(f"API /dags/status: Found status for DAG {dag_id}.")
            try:
                return SDKDagExecutionStatusModel(**cached_status_data)
            except Exception as e: # Pydantic validation or other errors
                logger.error(f"API /dags/status: Data for DAG {dag_id} from cache is not valid: {e}. Data: {cached_status_data}")
                raise HTTPException(status_code=500, detail="Invalid status data format in cache for DAG.")
        else:
            logger.warn(f"API /dags/status: No status found for DAG '{dag_id}' (key: '{status_key}')")
            # Return a valid SDKDagExecutionStatusModel indicating not found
            return SDKDagExecutionStatusModel(
                dag_id=dag_id, project_id=project_id, status="NOT_FOUND",
                message="DAG status not found or not yet processed.",
                started_at=datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc).isoformat(), # Placeholder
                task_statuses=[]
            )

@app.post("/api/forgeiq/deployments/trigger", response_model=DeploymentTriggerResponse, status_code=202, tags=["Deployments"])
async def trigger_deployment_api_endpoint(request_data: DeploymentTriggerRequest):
    with _start_api_span("trigger_deployment", service_name=request_data.service_name, project_id=request_data.project_id, commit_sha=request_data.commit_sha):
        # ... (Logic as in response #70, using message_router_instance.dispatch)
        logger.info(f"API /deployments/trigger: ReqID '{request_data.request_id}' for service {request_data.service_name}")
        router_payload = request_data.model_dump() # Pydantic model to dict
        router_payload["triggered_by"] = request_data.triggered_by or f"APIClient/{SERVICE_NAME}"
        try:
            dispatch_success = await message_router_instance.dispatch(logical_message_type="RequestServiceDeployment", payload=router_payload)
            if dispatch_success: return DeploymentTriggerResponse(message="Deployment request accepted.", request_id=request_data.request_id)
            else: raise HTTPException(status_code=503, detail="Failed to dispatch deployment request.")
        except (MessageRouteNotFoundError, InvalidMessagePayloadError) as e: raise HTTPException(status_code=400, detail=str(e))
        except Exception as e: logger.error(f"API /deployments/trigger: Unexpected error for ReqID {request_data.request_id}: {e}", exc_info=True); raise HTTPException(status_code=500, detail="Internal server error.")


@app.get("/api/forgeiq/projects/{project_id}/services/{service_name}/deployments/{deployment_request_id}/status",
         response_model=SDKDeploymentStatusModel, tags=["Deployments"])
async def get_deployment_status_api_endpoint(project_id: str, service_name: str, deployment_request_id: str):
    with _start_api_span("get_deployment_status", project_id=project_id, service_name=service_name, deployment_request_id=deployment_request_id):
        # ... (Logic as in response #70, using shared_memory_instance)
        logger.info(f"API /deployments/status: RequestID '{deployment_request_id}'")
        if not shared_memory_instance.redis_client: raise HTTPException(status_code=503, detail="Shared memory unavailable.")
        status_key = f"deployment:{deployment_request_id}:status_summary" # Key CI_CD_Agent writes to
        cached_status_data = await shared_memory_instance.get_value(status_key, expected_type=dict)
        if cached_status_data:
            try: return SDKDeploymentStatusModel(**cached_status_data)
            except Exception as e: logger.error(f"API /deployments/status: Invalid data for req {deployment_request_id}: {e}"); raise HTTPException(status_code=500, detail="Invalid status data format.")
        else:
            logger.warn(f"API /deployments/status: No status for req '{deployment_request_id}' (key: '{status_key}')")
            return SDKDeploymentStatusModel(request_id=deployment_request_id, project_id=project_id, service_name=service_name, status="NOT_FOUND", message="Deployment status not found.")


# === NEW Build System API Endpoints (from response #70) ===

@app.get("/api/forgeiq/projects", tags=["Projects"], summary="List all configured projects")
async def list_all_projects_endpoint() -> List[Dict[str, Any]]: # Define a ProjectSummaryModel in api_models.py later
    with _start_api_span("list_all_projects"):
        logger.info("API: Request to list all projects.")
        # Uses get_all_project_configs from core.build_system_config
        all_configs = await get_all_project_configs() # This is async now
        project_summaries = []
        for project_id, config_data in all_configs.get("projects", {}).items():
            project_summaries.append({
                "id": project_id,
                "name": project_id.replace("_", " ").title(), # Simple name generation
                "description": config_data.get("description", "N/A"),
                # "status": "UNKNOWN", # TODO: Fetch last build/deploy status for each project summary
                # "last_activity_ts": None
            })
        return project_summaries


@app.get("/api/forgeiq/projects/{project_id}/build-config", response_model=ProjectConfigResponse, tags=["Build System"])
async def get_project_config_api_endpoint(project_id: str):
    with _start_api_span("get_project_build_config", project_id=project_id):
        logger.info(f"API: Request for build config for project '{project_id}'")
        config_data = await get_core_project_config(project_id) # This is async
        if config_data:
            return ProjectConfigResponse(project_id=project_id, configuration=config_data) # type: ignore
        raise HTTPException(status_code=404, detail=f"Build configuration not found for project '{project_id}'")

@app.get("/api/forgeiq/projects/{project_id}/build-graph", response_model=BuildGraphResponse, tags=["Build System"])
async def get_project_build_graph_api_endpoint(project_id: str):
    with _start_api_span("get_project_build_graph", project_id=project_id):
        logger.info(f"API: Request for build graph for project '{project_id}'")
        # Your core.build_graph.get_project_dag returns List[str] (task sequence)
        tasks_sequence = get_project_dag(project_id) # This is synchronous
        if not tasks_sequence and project_id not in PROJECT_GRAPH: # Check if project known to build_graph
             raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in build graph definitions.")
        return BuildGraphResponse(project_id=project_id, tasks_sequence=tasks_sequence)

@app.get("/api/forgeiq/tasks", response_model=TaskListResponse, tags=["Build System"])
async def list_tasks_api_endpoint(project_id: Optional[str] = Query(None)):
    with _start_api_span("list_tasks", project_id=project_id or "all"):
        # Uses TASK_COMMANDS from your core.task_runner
        logger.info(f"API: Request to list tasks (project_id: {project_id or 'all'})")
        tasks_output: List[TaskDefinitionModel] = []
        # For now, TASK_COMMANDS is global. Filter by project_id if your task defs become project-specific.
        for task_name, command_details in TASK_COMMANDS.items():
            tasks_output.append(TaskDefinitionModel(
                task_name=task_name, command_details=command_details,
                description=f"Executes predefined '{task_name}' operation."
            ))
        return TaskListResponse(tasks=tasks_output)

@app.get("/api/forgeiq/tasks/{task_name}", response_model=TaskDefinitionModel, tags=["Build System"])
async def get_task_details_api_endpoint(task_name: str): # project_id: Optional[str] = Query(None)
    with _start_api_span("get_task_details", task_name=task_name):
        logger.info(f"API: Request for details of task '{task_name}'")
        if task_name in TASK_COMMANDS:
            return TaskDefinitionModel(
                task_name=task_name, command_details=TASK_COMMANDS[task_name],
                description=f"Details for predefined task: {task_name}"
            )
        raise HTTPException(status_code=404, detail=f"Task '{task_name}' not defined.")

# === NEW Endpoints for UI ===
@app.get("/api/forgeiq/agents", tags=["Agents"], summary="List all registered agents")
async def list_agents_endpoint() -> List[AgentRegistrationInfo]: # Using AgentRegistrationInfo TypedDict from interfaces
    with _start_api_span("list_agents"):
        logger.info("API: Request to list all agents.")
        if not shared_memory_instance.redis_client: # AgentRegistry V0.2 would use SharedMemory
            logger.error("API /agents: AgentRegistry cannot function, SharedMemory (Redis) unavailable.")
            raise HTTPException(status_code=503, detail="Agent registry service unavailable.")

        # This assumes AgentRegistry V0.2 persists its data to SharedMemoryStore (Redis)
        # in a queryable way, or AgentRegistry itself needs an API.
        # For now, let's assume AgentRegistry has a method that reads from SharedMemory
        # or that SharedMemory itself can list keys by a pattern.
        # This is a placeholder for how AgentRegistry data is exposed.
        # Conceptual:
        # agent_registry_data = await shared_memory_instance.get_value("agent_registry:all", expected_type=list)
        # if agent_registry_data: return agent_registry_data

        # Simpler for V0.1: If AgentRegistry were a singleton in this process (not ideal for scale)
        # from core.agent_registry import AgentRegistry # This assumes a singleton or accessible instance
        # agent_registry_service = AgentRegistry() # BAD: new instance each time
        # return agent_registry_service.list_all_agents() # list_all_agents needs to be async or run in thread
        # For now, return mock data as AgentRegistry isn't easily queryable via API yet.
        logger.warning("API /agents: Returning mock agent data. AgentRegistry needs API exposure or Redis persistence for real data.")
        mock_agents: List[AgentRegistrationInfo] = [
            {"agent_id": "codenav_1", "agent_type": "CodeNavAgent", "status": "active", "capabilities": [{"name":"semantic_search"}], "endpoints": [{"type":"http", "address":"codenav:8001"}], "last_seen_timestamp": datetime.datetime.utcnow().isoformat()+"Z", "metadata":{}}, #type: ignore
            {"agent_id": "planagent_1", "agent_type": "PlanAgent", "status": "active", "capabilities": [{"name":"dag_execution"}], "endpoints": [{"type":"event_bus", "address":"N/A"}], "last_seen_timestamp": datetime.datetime.utcnow().isoformat()+"Z", "metadata":{}}, #type: ignore
        ]
        return mock_agents


@app.get("/api/forgeiq/security/scan-results", tags=["Security"], summary="Get security scan results")
async def get_security_scan_results_endpoint(
    project_id: Optional[str] = Query(None),
    scan_type: Optional[str] = Query(None),
    min_severity: Optional[str] = Query(None), # e.g., HIGH, CRITICAL
    limit: int = Query(50, ge=1, le=200)
) -> Dict[str, List[SecurityScanResultEvent]]: # Returns list of SecurityScanResultEvent TypedDicts
    with _start_api_span("get_security_scan_results", project_id=project_id, scan_type=scan_type, min_severity=min_severity):
        logger.info(f"API: Request for security scan results. Project: {project_id}, Type: {scan_type}, Severity: {min_severity}")
        # TODO: Query SharedMemoryStore or a dedicated findings DB where SecurityAgent writes results.
        # Key pattern might be: "security_scan:{project_id}:{commit_sha_or_artifact_id}:result"
        # For now, mock data:
        logger.warning("API /security/scan-results: Returning mock data. Integration with SecurityAgent's persisted results needed.")
        mock_results: List[SecurityScanResultEvent] = []
        if project_id != "project_no_findings": # Simulate one project having findings
            mock_results.append(SecurityScanResultEvent(
                event_type="SecurityScanResultEvent", triggering_event_id=str(uuid.uuid4()),
                project_id=project_id or "project_alpha", commit_sha="abcdef1", artifact_name=None,
                scan_type=scan_type or "SAST_PYTHON_BANDIT", tool_name="bandit", status="COMPLETED_WITH_FINDINGS",
                findings=[
                    SecurityFinding(finding_id="B101_example", rule_id="B101", severity="HIGH", description="assert_used", file_path="app/main.py", line_number=42, code_snippet="assert False", tool_name="bandit", confidence="HIGH", more_info_url="https://bandit.readthedocs.io/en/latest/plugins/b101_assert_used.html", remediation="Avoid using assert for runtime checks.") # type: ignore
                ],
                summary="1 high severity finding.", timestamp=datetime.datetime.utcnow().isoformat()+"Z"
            ))
        return {"scan_results": mock_results}

@app.get("/api/forgeiq/governance/alerts", tags=["Governance"], summary="Get governance alerts")
async def get_governance_alerts_endpoint(
    alert_type: Optional[str] = Query(None), # e.g., SLAViolationEvent, PolicyViolation
    min_severity: Optional[str] = Query(None), # CRITICAL, HIGH, etc.
    limit: int = Query(25, ge=1, le=100)
) -> Dict[str, List[Any]]: # Returns list of SLAViolationEvent or GovernanceAlertEvent
    with _start_api_span("get_governance_alerts", alert_type=alert_type, min_severity=min_severity):
        logger.info(f"API: Request for governance alerts. Type: {alert_type}, Severity: {min_severity}")
        # TODO: Query SharedMemoryStore or a dedicated store where GovernanceAgent writes alerts.
        # Key pattern: "governance:alerts:{alert_id}" or list "governance:alerts_by_type:{type}"
        logger.warning("API /governance/alerts: Returning mock data. Integration with GovernanceAgent's persisted alerts needed.")
        mock_alerts: List[Any] = []
        if alert_type != "NoAlertsPlease":
            mock_alerts.append(SLAViolationEvent(
                event_type="SLAViolationEvent", alert_id=str(uuid.uuid4()), timestamp=datetime.datetime.utcnow().isoformat()+"Z",
                sla_name="dag_exec_time", metric_name="duration_seconds", observed_value=5000, threshold_value=3600,
                project_id="project_alpha", entity_id="dag_xyz", details="DAG execution exceeded SLA."
            ))
        return {"alerts": mock_alerts}

# Uvicorn startup handled by Dockerfile CMD using the 'app' instance from this file.
# To run locally:
# Set PYTHONPATH=. uvicorn apps.forgeiq-backend.app.main:app --reload --port 80
