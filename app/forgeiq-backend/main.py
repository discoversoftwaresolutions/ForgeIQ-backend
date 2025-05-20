# =====================================================================
# 📁 app/forgeiq-backend/main.py (V0.5 - Auth & Dependency Injection)
# =====================================================================
import os
import json
import datetime
import uuid
import logging
import asyncio
from contextlib import asynccontextmanager # For lifespan events
from typing import Dict, Any, Optional, List
from .index import TASK_COMMANDS
from fastapi import FastAPI, HTTPException, Body, Query, Depends, Security
from app.forgeiq_backend.api_models import CodeGenerationRequest  # ✅ Correct module lookup

# --- Observability Setup (as before) ---
SERVICE_NAME = "ForgeIQ_Backend_Py"
# ... (full logging and tracer setup as in response #87, ensuring FastAPIInstrumentor is imported)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format=f'%(asctime)s - %(levelname)s - [{SERVICE_NAME}] - %(name)s - %(message)s')
_tracer = None; _trace_api = None
try:
    from opentelemetry import trace as otel_trace_api
    from core.observability.tracing import setup_tracing
    _tracer = setup_tracing(SERVICE_NAME)
    _trace_api = otel_trace_api
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
except ImportError: logging.getLogger(SERVICE_NAME).warning("Tracing/FastAPIInstrumentor failed.")
logger = logging.getLogger(__name__)
# --- End Observability Setup ---

# Import core modules for type hinting or direct use if not injected (though injection is preferred)
from core.build_graph import get_project_dag, PROJECT_GRAPH
from core.task_runner import TASK_COMMANDS
from core.build_system_config import get_project_build_config as get_core_project_build_config

# Import Pydantic models and dependency providers
from .api_models import (
    PipelineGenerateRequest, PipelineGenerateResponse,
    DeploymentTriggerRequest, DeploymentTriggerResponse,
    SDKDagExecutionStatusModel, SDKDeploymentStatusModel,
    ProjectConfigResponse, BuildGraphResponse, TaskListResponse, TaskDefinitionModel,
    CodeGenerationRequest, CodeGenerationResponse, GeneratedCodeOutput # CodeGenerationPrompt used by CodeGenerationRequest
)
from .dependencies import ( # <<< NEW IMPORTS FOR DEPENDENCIES
    get_api_key,
    get_event_bus,
    get_message_router,
    get_shared_memory_store,
    get_private_intel_client,
    init_core_services, # To call on startup
    close_core_services # To call on shutdown
)
# Import specific core classes if needed for type hints in Depends()
from core.event_bus.redis_bus import EventBus
from core.message_router import MessageRouter
from core.shared_memory import SharedMemoryStore
import httpx # For type hint of private_intel_http_client

# --- FastAPI App Lifespan Manager (for initializing/closing resources) ---
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    # Startup: Initialize core services
    await init_core_services()
    yield
    # Shutdown: Close core services
    await close_core_services()

app = FastAPI(
    title="ForgeIQ Backend API (Python)",
    description="API Gateway for ForgeIQ Agentic Build System.",
    version="2.0.5", # Or increment to reflect these changes
    lifespan=lifespan # <<< ATTACH LIFESPAN HANDLER
)

if _tracer: # Apply FastAPI OTel Instrumentation (after app is created)
    try: FastAPIInstrumentor.instrument_app(app, tracer_provider=_tracer.provider if _tracer else None) #type: ignore
    except Exception as e_otel_fastapi: logger.error(f"Failed to instrument FastAPI with OTel: {e_otel_fastapi}")


def _start_api_span(name: str, **attrs): # Helper from before
    # ... (same as before)
    if _tracer:
        span = _tracer.start_as_current_span(f"api.{name}")
        for k,v in attrs.items(): span.set_attribute(k,v)
        return span
    class NoOpSpanCM:
        def __enter__(self): return None
        def __exit__(self,et,ev,tb): pass
    return NoOpSpanCM()


# === API Endpoints (Now with Dependency Injection and Security) ===

@app.get("/api/health", tags=["Health"], summary="Health check for ForgeIQ Backend")
async def health_check(event_bus: EventBus = Depends(get_event_bus)): # Example of injecting EventBus
    with _start_api_span("health_check"):
        logger.info("API /api/health invoked")
        redis_ok = False
        if event_bus.redis_client: # Check EventBus's client
            try:
                await asyncio.to_thread(event_bus.redis_client.ping)
                redis_ok = True
            except Exception as e_redis: logger.error(f"Health check: Redis ping failed: {e_redis}")
        return {
            "service_name": SERVICE_NAME, "status": "healthy",
            "timestamp": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            "environment": os.getenv("APP_ENV", "development"),
            "redis_event_bus_status": "connected" if redis_ok else "disconnected_or_error"
        }

@app.post("/api/forgeiq/pipelines/generate", 
          response_model=PipelineGenerateResponse, 
          status_code=202, 
          tags=["Pipelines"],
          dependencies=[Security(get_api_key)]) # <<< SECURED ENDPOINT
async def generate_pipeline_from_prompt_endpoint(
    request_data: PipelineGenerateRequest,
    message_router: MessageRouter = Depends(get_message_router) # <<< INJECTED
):
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
            dispatch_success = await message_router.dispatch(
                logical_message_type="GeneratePipelineFromPrompt", payload=router_payload
            )
            if dispatch_success:
                return PipelineGenerateResponse(message="Pipeline generation request accepted.", request_id=request_data.request_id)
            else:
                raise HTTPException(status_code=503, detail="Failed to dispatch pipeline generation request.")
        except (MessageRouteNotFoundError, InvalidMessagePayloadError) as e: # Assuming these are imported with MessageRouter
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"API /pipelines/generate: Unexpected error for ReqID {request_data.request_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error.")


@app.post("/api/forgeiq/code/generate", 
          response_model=CodeGenerationResponse, 
          tags=["Code Generation (via Private Intelligence)"],
          summary="Generate specialized algorithms or code.",
          dependencies=[Security(get_api_key)]) # <<< SECURED ENDPOINT
async def generate_algorithm_via_private_intel_endpoint(
    request_data: CodeGenerationRequest,
    private_intel_client: httpx.AsyncClient = Depends(get_private_intel_client) # <<< INJECTED
):
    flow_id = request_data.request_id
    span_attrs = {"codegen.request_id": flow_id, "codegen.project_id": request_data.prompt_details.project_id}
    with _start_api_span("generate_algorithm_private_intel", **span_attrs) as span: #type: ignore
        logger.info(f"API /code/generate: Forwarding to Private Intel. ReqID '{flow_id}', Lang: {request_data.prompt_details.language}")

        project_id_for_algo = request_data.prompt_details.project_id
        if not project_id_for_algo:
            raise HTTPException(status_code=400, detail="project_id is required in prompt_details for algorithm generation.")

        dag_sequence_for_project: List[str] = get_project_dag(project_id_for_algo) # Synchronous call

        algorithm_agent_payload = {
            "project": project_id_for_algo, "dag": dag_sequence_for_project,
            "telemetry": {"source_prompt": request_data.prompt_details.prompt_text, "requested_by_api": True}
        }
        if request_data.prompt_details.additional_context:
            algorithm_agent_payload.update(request_data.prompt_details.additional_context)

        private_api_endpoint = "/generate" # Endpoint on your private AlgorithmAgent
        try:
            response = await private_intel_client.post(private_api_endpoint, json=algorithm_agent_payload) # Use injected client
            response.raise_for_status()
            private_api_response_data = response.json()
            generated_algo_code = private_api_response_data.get("code")

            if generated_algo_code is not None:
                # ... (construct 'output' as in response #87)
                output = GeneratedCodeOutput(
                    generated_code=str(generated_algo_code), language_detected="python", # Example
                    model_used=private_api_response_data.get("model_used", "proprietary_model"),
                    finish_reason=private_api_response_data.get("finish_reason"),
                    usage_tokens=private_api_response_data.get("usage_tokens")
                )
                return CodeGenerationResponse(request_id=flow_id, status="success", output=output)
            else:
                raise HTTPException(status_code=500, detail="Private AlgorithmAgent returned no 'code'.")
        # ... (httpx.HTTPStatusError and other specific error handling as in response #87) ...
        except httpx.HTTPStatusError as e_http:
            err_msg = f"Error calling AlgorithmAgent: {e_http.response.status_code} - {e_http.response.text[:200]}"
            logger.error(err_msg, exc_info=True)
            raise HTTPException(status_code=502, detail=f"Error with AlgorithmAgent: HTTP {e_http.response.status_code}")
        except Exception as e_call: # General catch for private API call
            err_msg = f"Error during AlgorithmAgent call: {e_call}"
            logger.error(err_msg, exc_info=True)
            raise HTTPException(status_code=502, detail=f"Error calling AlgorithmAgent: {str(e_call)}")


# === Status Endpoints (now using injected SharedMemoryStore) ===
@app.get("/api/forgeiq/projects/{project_id}/dags/{dag_id}/status", 
         response_model=SDKDagExecutionStatusModel, tags=["Pipelines"],
         dependencies=[Security(get_api_key)]) # <<< SECURED
async def get_dag_status_api_endpoint(
    project_id: str, dag_id: str,
    shared_memory: SharedMemoryStore = Depends(get_shared_memory_store) # <<< INJECTED
):
    with _start_api_span("get_dag_status", project_id=project_id, dag_id=dag_id):
        status_key = f"dag_execution:{dag_id}:status_summary" # Key PlanAgent writes to
        cached_status_data = await shared_memory.get_value(status_key, expected_type=dict)
        if cached_status_data:
            try: return SDKDagExecutionStatusModel(**cached_status_data)
            except Exception as e: raise HTTPException(status_code=500, detail="Invalid cached DAG status format.")
        else:
            # Return a NOT_FOUND that matches the model structure
            return SDKDagExecutionStatusModel(
                dag_id=dag_id, project_id=project_id, status="NOT_FOUND",
                message="DAG status not found or not yet processed.",
                started_at=datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc).isoformat(),
                task_statuses=[]
            )

# ... (Similarly refactor other GET status endpoints and POST trigger endpoints
#      to use `Depends(get_message_router)`, `Depends(get_shared_memory_store)`
#      and add `dependencies=[Security(get_api_key)]`) ...

# Example for /api/forgeiq/deployments/trigger
@app.post("/api/forgeiq/deployments/trigger", 
          response_model=DeploymentTriggerResponse, status_code=202, tags=["Deployments"],
          dependencies=[Security(get_api_key)])
async def trigger_deployment_api_endpoint(
    request_data: DeploymentTriggerRequest,
    message_router: MessageRouter = Depends(get_message_router)
):
    # ... (logic from response #70, using injected message_router) ...
    with _start_api_span("trigger_deployment", service_name=request_data.service_name, project_id=request_data.project_id):
        logger.info(f"API /deployments/trigger: ReqID '{request_data.request_id}' for service {request_data.service_name}")
        router_payload = request_data.model_dump()
        router_payload["triggered_by"] = request_data.triggered_by or f"APIClient/{SERVICE_NAME}"
        try:
            dispatch_success = await message_router.dispatch(logical_message_type="RequestServiceDeployment", payload=router_payload)
            if dispatch_success: return DeploymentTriggerResponse(message="Deployment request accepted.", request_id=request_data.request_id)
            else: raise HTTPException(status_code=503, detail="Failed to dispatch deployment request.")
        except (MessageRouteNotFoundError, InvalidMessagePayloadError) as e: raise HTTPException(status_code=400, detail=str(e))
        except Exception as e: logger.error(f"API /deployments/trigger error: {e}", exc_info=True); raise HTTPException(status_code=500, detail="Internal error.")


# ... (Implement other GET endpoints like list_all_projects, get_project_build_config, etc.
#      using Depends(get_shared_memory_store) or relevant core config modules,
#      and add `dependencies=[Security(get_api_key)]` to protect them) ...

# Example for /api/forgeiq/projects/{project_id}/build-config
@app.get("/api/forgeiq/projects/{project_id}/build-config", 
         response_model=ProjectConfigResponse, tags=["Build System"],
         dependencies=[Security(get_api_key)])
async def get_project_config_api_endpoint(project_id: str): # No specific service injection needed if get_core_project_config is simple import
    with _start_api_span("get_project_build_config", project_id=project_id):
        config_data = await get_core_project_config(project_id) # Assuming this is async as defined
        if config_data: return ProjectConfigResponse(project_id=project_id, configuration=config_data) # type: ignore
        raise HTTPException(status_code=404, detail=f"Build config not found for project '{project_id}'")

# Uvicorn startup is handled by the Dockerfile's CMD instruction.
# In apps/forgeiq-backend/app/main.py
# Add Pydantic models for request/response in api_models.py
class MCPStrategyRequest(BaseModel):
    current_dag_info: Optional[Dict[str, Any]] = None

class MCPStrategyResponse(BaseModel):
    project_id: str
    strategy_id: Optional[str] = None
    # new_dag_definition: Optional[SDKDagDefinitionModel] = None # If MCP returns a full new DAG
    directives: Optional[List[str]] = None
    status: str
    message: Optional[str] = None

@app.post("/api/forgeiq/mcp/optimize-strategy/{project_id}", 
          response_model=MCPStrategyResponse, 
          tags=["MCP Integration"],
          dependencies=[Security(get_api_key)])
async def mcp_optimize_strategy_endpoint(
    project_id: str, 
    request_data: MCPStrategyRequest,
    private_intel_client: httpx.AsyncClient = Depends(get_private_intel_client) # Use the existing client for private stack
):
    with _start_api_span("mcp_optimize_strategy", project_id=project_id) as span:
        logger.info(f"API: Forwarding optimize strategy request for '{project_id}' to private MCP.")

        # Payload for your private MCP's API endpoint (e.g., /mcp/strategize)
        # This depends on what your private MCP's API (via intelligence_bridge.py) expects
        mcp_payload = {
            "project_id": project_id,
            "current_dag_info": request_data.current_dag_info,
            "goal": "optimize_build_strategy"
        }
        # Example: private_mcp_endpoint = "/mcp/strategize"
        private_mcp_endpoint = "/mcp/optimize_build_strategy" # Or whatever your private MCP exposes

        try:
            response = await private_intel_client.post(private_mcp_endpoint, json=mcp_payload)
            response.raise_for_status()
            mcp_response_data = response.json()
            logger.info(f"Response from private MCP for '{project_id}': {message_summary(mcp_response_data)}")

            # Adapt mcp_response_data to MCPStrategyResponse
            return MCPStrategyResponse(
                project_id=project_id,
                strategy_id=mcp_response_data.get("strategy_id"),
                # new_dag_definition=mcp_response_data.get("new_dag"),
                directives=mcp_response_data.get("directives"),
                status=mcp_response_data.get("status", "unknown_mcp_status"),
                message=mcp_response_data.get("message")
            )
        # ... (Robust error handling for httpx call as in other endpoints) ...
        except httpx.HTTPStatusError as e_http: # ... (handle error) ...
            raise HTTPException(status_code=502, detail=f"MCP service error: {e_http.response.status_code}")
        except Exception as e_call: # ... (handle error) ...
            raise HTTPException(status_code=500, detail=f"Internal error calling MCP: {str(e_call)}")
# =====================================================================
# 📁 app/forgeiq-backend/app/main.py (additions for MCP endpoint)
# =====================================================================
# ... (existing imports: os, json, datetime, uuid, logging, asyncio, httpx, FastAPI, Depends, Security) ...
# ... (existing Pydantic models from .api_models, including the new MCP ones) ...
from .api_models import MCPStrategyApiRequest, MCPStrategyApiResponse, MCPStrategyApiDetails # Ensure these are imported
# ... (existing core service initializations: event_bus_instance, message_router_instance, shared_memory_instance) ...
# ... (existing private_intel_http_client for AlgorithmAgent) ...

# --- NEW MCP API Endpoint ---
@app.post("/api/forgeiq/mcp/optimize-strategy/{project_id}", 
          response_model=MCPStrategyApiResponse, 
          tags=["MCP Integration"],
          summary="Request build strategy optimization from the private Master Control Program.",
          dependencies=[Depends(get_api_key)]) # Secured endpoint
async def mcp_optimize_strategy_endpoint(
    project_id: str, 
    request_data: MCPStrategyApiRequest, # Uses Pydantic model for request body
    # Use the same httpx client configured for the private intelligence stack
    intel_stack_client: httpx.AsyncClient = Depends(get_private_intel_client) 
):
    span_attrs = {"project_id": project_id, "mcp.goal": request_data.optimization_goal or "default"}
    with _start_api_span("mcp_optimize_strategy", **span_attrs) as span: # type: ignore
        logger.info(f"API: Forwarding optimize build strategy request for project '{project_id}' to private MCP.")

        if not intel_stack_client: # Check if client was initialized
            logger.error("Private Intelligence API client (for MCP) not initialized.")
            if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "MCP client missing"))
            raise HTTPException(status_code=503, detail="MCP service client not available.")

        # Construct payload for your private MCP's API endpoint.
        # This payload needs to match what your private MCP/controller.py (via intelligence_bridge.py) expects.
        # For example, it might expect project_id in the path and other details in the body.
        mcp_payload = {
            "project_id": project_id, # MCP might re-verify or use this
            "current_dag_snapshot": request_data.current_dag_snapshot,
            "optimization_goal": request_data.optimization_goal,
            "additional_context": request_data.additional_mcp_context
            # Add any other fields your private MCP API requires
        }

        # Replace with the actual endpoint path on your private intelligence service for MCP strategy requests
        private_mcp_api_endpoint = "/mcp/request-strategy" # Example endpoint path

        try:
            logger.debug(f"Calling private MCP at {private_mcp_api_endpoint} for project '{project_id}'.")
            if _tracer and span: 
                span.set_attribute("private_intel.target_service", "MCP")
                span.set_attribute("private_intel.target_endpoint", private_mcp_api_endpoint)

            response = await intel_stack_client.post(private_mcp_api_endpoint, json=mcp_payload)
            response.raise_for_status() 

            mcp_response_data = response.json() # Response from your private MCP
            logger.info(f"Response from private MCP for '{project_id}': {message_summary(mcp_response_data)}")

            # Adapt mcp_response_data to the MCPStrategyApiResponse Pydantic model
            # This depends on the structure of the response from your private MCP.
            strategy_details = None
            if mcp_response_data.get("status") == "strategy_provided": # Example status from MCP
                strategy_details = MCPStrategyApiDetails(
                    strategy_id=mcp_response_data.get("strategy_id"),
                    new_dag_definition_raw=mcp_response_data.get("new_dag_definition"), # Or "optimized_dag"
                    directives=mcp_response_data.get("directives"),
                    mcp_metadata=mcp_response_data.get("mcp_execution_details")
                )

            api_response_status = mcp_response_data.get("status", "MCP_RESPONSE_UNKNOWN")
            if _trace_api and span: 
                span.set_attribute("mcp.response_status", api_response_status)
                span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))

            return MCPStrategyApiResponse(
                project_id=project_id,
                status=api_response_status,
                message=mcp_response_data.get("message"),
                strategy_details=strategy_details
            )

        except httpx.HTTPStatusError as e_http:
            err_msg = f"Error calling private MCP service: {e_http.response.status_code} - {e_http.response.text[:200]}"
            logger.error(err_msg, exc_info=True)
            if _trace_api and span: span.record_exception(e_http); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "MCP HTTP error"))
            raise HTTPException(status_code=502, detail=f"Error communicating with MCP service: HTTP {e_http.response.status_code}")
        except Exception as e_call:
            err_msg = f"Error during private MCP call for project '{project_id}': {e_call}"
            logger.error(err_msg, exc_info=True)
            if _trace_api and span: span.record_exception(e_call); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "MCP call failed"))
            raise HTTPException(status_code=500, detail=f"Internal error while calling MCP service: {str(e_call)}")
# In app/forgeiq-backend/app/main.py
# ... (existing imports, including private_intel_http_client and get_private_intel_client) ...
from .api_models import ApplyAlgorithmRequest, ApplyAlgorithmResponse # Add these

@app.post("/api/forgeiq/algorithms/apply", 
          response_model=ApplyAlgorithmResponse, 
          tags=["Proprietary Algorithms"],
          summary="Apply a named proprietary algorithm via the Private Intelligence Stack.",
          dependencies=[Depends(get_api_key)])
async def apply_proprietary_algorithm_endpoint(
    request_data: ApplyAlgorithmRequest,
    intel_stack_client: httpx.AsyncClient = Depends(get_private_intel_client)
):
    span_attrs = {"algorithm_id": request_data.algorithm_id, "project_id": request_data.project_id}
    with _start_api_span("apply_proprietary_algorithm", **span_attrs) as span: # type: ignore
        logger.info(f"API: Request to apply proprietary algorithm '{request_data.algorithm_id}' for project '{request_data.project_id}'.")

        if not intel_stack_client:
            raise HTTPException(status_code=503, detail="Private Intelligence service client not available.")

        # Payload for your private stack's /invoke_proprietary_algorithm endpoint
        private_api_payload = {
            "algorithm_id": request_data.algorithm_id,
            "project_id": request_data.project_id,
            "context_data": request_data.context_data
        }
        private_api_endpoint = "/invoke_proprietary_algorithm" # Matches endpoint on private AlgorithmAgent/MCP

        try:
            response = await intel_stack_client.post(private_api_endpoint, json=private_api_payload)
            response.raise_for_status()
            private_response_data = response.json() # This is ApplyProprietaryAlgorithmResponse structure

            return ApplyAlgorithmResponse(**private_response_data) # Map directly if compatible
        # ... (robust httpx error handling as in other private API calls) ...
        except httpx.HTTPStatusError as e_http: # ... (handle error)
            raise HTTPException(status_code=502, detail=f"Error from Private Algorithm Service: HTTP {e_http.response.status_code}")
        except Exception as e_call: # ... (handle error)
            raise HTTPException(status_code=500, detail=f"Internal error calling Private Algorithm Service: {str(e_call)}")
