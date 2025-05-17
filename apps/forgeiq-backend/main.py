# =====================================================================
# 📁 apps/forgeiq-backend/main.py (V0.5 - Calls specific AlgorithmAgent)
# =====================================================================
import os
import json
import datetime
import uuid
import logging
import asyncio
from typing import Dict, Any, Optional, List

import httpx # For calling private intelligence API
from fastapi import FastAPI, HTTPException, Body, Query

# --- Observability Setup (as before) ---
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

# Core service imports
from core.event_bus.redis_bus import EventBus
from core.message_router import MessageRouter # MessageRouteNotFoundError, InvalidMessagePayloadError
from core.shared_memory import SharedMemoryStore
from core.build_graph import get_project_dag # To fetch DAG for AlgorithmAgent
# ... other core imports ...

from .api_models import ( 
    CodeGenerationRequest, CodeGenerationResponse, GeneratedCodeOutput, CodeGenerationPrompt,
    # ... other existing API models ...
)

# --- Configuration for Private Intelligence Stack API ---
PRIVATE_INTEL_API_BASE_URL = os.getenv("PRIVATE_INTELLIGENCE_API_BASE_URL")
PRIVATE_INTEL_API_KEY = os.getenv("PRIVATE_INTELLIGENCE_API_KEY")

private_intel_http_client: Optional[httpx.AsyncClient] = None
if PRIVATE_INTEL_API_BASE_URL:
    headers = {"Content-Type": "application/json"}
    if PRIVATE_INTEL_API_KEY:
        headers["Authorization"] = f"Bearer {PRIVATE_INTEL_API_KEY}"
    try:
        private_intel_http_client = httpx.AsyncClient(base_url=PRIVATE_INTEL_API_BASE_URL, headers=headers, timeout=180.0) # Longer timeout for AI
        logger.info(f"HTTP client for Private Intelligence API initialized, targeting: {PRIVATE_INTEL_API_BASE_URL}")
    except Exception as e_intel_client:
        logger.error(f"Failed to initialize HTTP client for Private Intelligence API: {e_intel_client}", exc_info=True)
else:
    logger.warning("PRIVATE_INTELLIGENCE_API_BASE_URL not set. Specialized code/algorithm generation will be unavailable.")
# --- End Private Intelligence Stack API Config ---

# Initialize other core services (EventBus, MessageRouter, SharedMemoryStore) as before
# ...
event_bus_instance = EventBus()
message_router_instance = MessageRouter(event_bus_instance) # Requires event_bus_instance
shared_memory_instance = SharedMemoryStore()


app = FastAPI(
    title="ForgeIQ Backend API (Python)",
    description="API Gateway for ForgeIQ, integrates with Private AlgorithmAgent.",
    version="2.0.5" 
)
if _tracer: # Apply FastAPI OTel Instrumentation
    try: FastAPIInstrumentor.instrument_app(app, tracer_provider=_tracer.provider if _tracer else None) #type: ignore
    except Exception as e_otel_fastapi: logger.error(f"Failed to instrument FastAPI: {e_otel_fastapi}")

def _start_api_span(name: str, **attrs): # Helper from before
    if _tracer:
        span = _tracer.start_as_current_span(f"api.{name}")
        for k,v in attrs.items(): span.set_attribute(k,v)
        return span
    class NoOpSpanCM:
        def __enter__(self): return None
        def __exit__(self,et,ev,tb): pass
    return NoOpSpanCM()

# ... (Existing /api/health and other API endpoints as defined in response #70 & #72 remain) ...

# === MODIFIED Code Generation API Endpoint ===
# This endpoint now primarily acts as a proxy to your private AlgorithmAgent for specific tasks.
@app.post("/api/forgeiq/code/generate", 
          response_model=CodeGenerationResponse, 
          tags=["Code Generation (via AlgorithmAgent)"],
          summary="Generate specialized algorithms or code using the Private Intelligence Stack.")
async def generate_algorithm_via_private_intel_endpoint(request_data: CodeGenerationRequest):
    # This endpoint now expects prompts suitable for your private AlgorithmAgent.
    # The `request_data.prompt_details` should contain information that can be mapped
    # to the `AlgorithmContext` (project, dag, telemetry) your private agent expects.

    flow_id = request_data.request_id # Use the request_id as a flow identifier
    span_attrs = {"codegen.request_id": flow_id, "codegen.project_id": request_data.prompt_details.project_id}
    with _start_api_span("generate_algorithm_private_intel", **span_attrs) as span: # type: ignore

        logger.info(f"API /code/generate: Received request '{flow_id}' for project '{request_data.prompt_details.project_id}'. Forwarding to AlgorithmAgent.")

        if not private_intel_http_client:
            logger.error("Private Intelligence API client (for AlgorithmAgent) not initialized.")
            if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "AlgorithmAgent client not initialized"))
            raise HTTPException(status_code=503, detail="Algorithm generation service not available.")

        project_id_for_algo = request_data.prompt_details.project_id
        if not project_id_for_algo:
            logger.error(f"Request '{flow_id}': project_id is required in prompt_details to fetch DAG for AlgorithmAgent.")
            raise HTTPException(status_code=400, detail="project_id must be specified in prompt_details for algorithm generation.")

        # 1. Fetch or determine the DAG for the project
        # The private AlgorithmAgent's prompt seems to expect a DAG: "optimize this build DAG: {context['dag']}"
        # We use our core.build_graph.get_project_dag which returns a list of task strings (the sequence).
        # Your private AlgorithmAgent might expect a more detailed DAG structure (nodes with dependencies).
        # For now, we'll pass this sequence. This might need alignment.
        dag_sequence_for_project: List[str] = get_project_dag(project_id_for_algo)
        if not dag_sequence_for_project:
            logger.warning(f"Request '{flow_id}': No DAG sequence found for project '{project_id_for_algo}' via core.build_graph. Sending empty list to AlgorithmAgent.")

        # 2. Prepare telemetry (placeholder for V0.1)
        telemetry_data: Dict[str, Any] = {
            "source_prompt": request_data.prompt_details.prompt_text,
            "requested_by_sdk": True,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }

        # 3. Construct payload for the private AlgorithmAgent's /generate endpoint
        algorithm_agent_payload = {
            "project": project_id_for_algo,
            "dag": dag_sequence_for_project, # This is List[str]
            "telemetry": telemetry_data
            # The prompt for the LLM within AlgorithmAgent is constructed there.
            # The user_prompt_text from request_data.prompt_details might be used by AlgorithmAgent
            # as part of its internal context for building its own LLM prompt.
        }
        if request_data.prompt_details.additional_context: # Pass along any extra context
            algorithm_agent_payload.update(request_data.prompt_details.additional_context)


        private_api_endpoint = "/generate" # Endpoint in your private AlgorithmAgent

        try:
            logger.debug(f"Flow {flow_id}: Calling private AlgorithmAgent at {private_api_endpoint} with project '{project_id_for_algo}'.")
            if _tracer and span: 
                span.set_attribute("private_intel.target_endpoint", private_api_endpoint)
                span.set_attribute("private_intel.payload_project", project_id_for_algo)
                span.set_attribute("private_intel.payload_dag_tasks_count", len(dag_sequence_for_project))

            response = await private_intel_http_client.post(private_api_endpoint, json=algorithm_agent_payload)
            response.raise_for_status() 

            private_api_response_data = response.json() # Expects {"reference": ref, "score": evaluated, "code": generated["code"]}
            logger.debug(f"Flow {flow_id}: Private AlgorithmAgent raw response: {message_summary(private_api_response_data, 300)}")

            generated_algo_code = private_api_response_data.get("code")
            algo_ref = private_api_response_data.get("reference")
            algo_score = private_api_response_data.get("score")

            if _tracer and span:
                span.set_attribute("private_intel.response.algo_ref", algo_ref or "N/A")
                span.set_attribute("private_intel.response.algo_score", str(algo_score))

            if generated_algo_code is not None:
                logger.info(f"Flow {flow_id}: Algorithm generated successfully by private AlgorithmAgent. Ref: {algo_ref}")
                output = GeneratedCodeOutput( # Reusing this model, but context is algorithm
                    generated_code=str(generated_algo_code),
                    language_detected="python", # Assuming AlgorithmAgent generates Python
                    model_used=f"ProprietaryAlgorithmAgent(score:{algo_score}, ref:{algo_ref})",
                    finish_reason="algorithm_generated",
                    usage_tokens=None # Not directly from a public LLM call here
                )
                if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                return CodeGenerationResponse(request_id=flow_id, status="success", output=output)
            else:
                err_msg = f"Private AlgorithmAgent returned no 'code' for ReqID '{flow_id}'."
                logger.error(err_msg)
                if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "AlgorithmAgent no code"))
                raise HTTPException(status_code=500, detail=err_msg)

        except httpx.HTTPStatusError as e_http:
            err_msg = f"Error calling private AlgorithmAgent (ReqID '{flow_id}'): {e_http.response.status_code} - {e_http.response.text[:200]}"
            logger.error(err_msg, exc_info=True)
            if _trace_api and span: span.record_exception(e_http); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Private AlgoAgent HTTP error"))
            raise HTTPException(status_code=502, detail=f"Error communicating with AlgorithmAgent service: HTTP {e_http.response.status_code}")
        except Exception as e_algo_call:
            err_msg = f"Error during private AlgorithmAgent call (ReqID '{flow_id}'): {e_algo_call}"
            logger.error(err_msg, exc_info=True)
            if _trace_api and span: span.record_exception(e_algo_call); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Private AlgoAgent call failed"))
            raise HTTPException(status_code=502, detail=f"Error communicating with AlgorithmAgent service: {str(e_algo_call)}")
    except HTTPException: 
        raise 
    except Exception as e_outer: 
        logger.error(f"Outer error in /code/generate endpoint for ReqID '{flow_id}': {e_outer}", exc_info=True)
        if _trace_api and span: span.record_exception(e_outer); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Outer endpoint error"))
        raise HTTPException(status_code=500, detail="Internal server error in code generation endpoint.")

# ... (Other existing API endpoints and Uvicorn startup via Dockerfile CMD)
# In apps/forgeiq-backend/app/main.py
# ... (other imports, including httpx, PRIVATE_INTEL_API_BASE_URL, private_intel_http_client) ...
from .api_models import OptimizeStrategyRequest, OptimizeStrategyResponse, OptimizedAlgorithmDetails
# from core.build_graph import get_project_dag # Might be needed if dag_representation needs to be fetched

@app.post("/api/forgeiq/projects/{project_id}/build-strategy/optimize",
          response_model=OptimizeStrategyResponse,
          tags=["Build Strategy Optimization"],
          summary="Request optimization of a build strategy using private AlgorithmAgent.")
async def optimize_build_strategy_endpoint(project_id: str, request_data: OptimizeStrategyRequest):
    span_attrs = {"project_id": project_id}
    with _start_api_span("optimize_build_strategy", **span_attrs) as span: # type: ignore
        logger.info(f"API: Optimizing build strategy for project '{project_id}'.")

        if not private_intel_http_client:
            logger.error("Private Intelligence API client (for AlgorithmAgent) not initialized.")
            if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "AlgorithmAgent client missing"))
            raise HTTPException(status_code=503, detail="Algorithm optimization service not available.")

        # Construct the AlgorithmContext payload for your private AlgorithmAgent
        # The 'dag' your AlgorithmAgent expects might be the List[str] from get_project_dag,
        # or the more structured DagDefinition. For now, using what request_data provides.
        algorithm_agent_payload = {
            "project": project_id,
            "dag": request_data.dag_representation, # This must match what your private AlgoAgent's /generate expects for 'dag'
            "telemetry": request_data.telemetry_data
        }

        private_api_endpoint = "/generate" # The endpoint on your private AlgorithmAgent

        try:
            logger.debug(f"Calling private AlgorithmAgent at {private_api_endpoint} for project '{project_id}'.")
            if _tracer and span: span.set_attribute("private_intel.target_endpoint", private_api_endpoint)

            # Use the existing private_intel_http_client
            response = await private_intel_http_client.post(private_api_endpoint, json=algorithm_agent_payload)
            response.raise_for_status()

            private_api_response_data = response.json() # Expects {"reference": ref, "score": evaluated, "code": generated_code}
            logger.info(f"Private AlgorithmAgent response: {message_summary(private_api_response_data)}")

            if "code" in private_api_response_data:
                optim_details = OptimizedAlgorithmDetails(
                    algorithm_reference=private_api_response_data.get("reference", "N/A"),
                    benchmark_score=private_api_response_data.get("score", 0.0),
                    generated_code_or_dag=private_api_response_data["code"]
                )
                if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                return OptimizeStrategyResponse(
                    message="Build strategy optimization processed successfully.",
                    optimization_details=optim_details,
                    status="completed"
                )
            else:
                err_msg = "Private AlgorithmAgent returned no 'code' in response."
                logger.error(err_msg)
                if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, err_msg))
                raise HTTPException(status_code=500, detail=err_msg)

        except httpx.HTTPStatusError as e_http:
            err_msg = f"Error calling AlgorithmAgent: {e_http.response.status_code} - {e_http.response.text[:200]}"
            logger.error(err_msg, exc_info=True)
            if _trace_api and span: span.record_exception(e_http); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "AlgoAgent HTTP error"))
            raise HTTPException(status_code=502, detail=f"Error with AlgorithmAgent service: HTTP {e_http.response.status_code}")
        except Exception as e_call:
            err_msg = f"Error during AlgorithmAgent call: {e_call}"
            logger.error(err_msg, exc_info=True)
            if _trace_api and span: span.record_exception(e_call); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "AlgoAgent call failed"))
            raise HTTPException(status_code=502, detail=f"Error calling AlgorithmAgent service: {str(e_call)}")
