# ===================================================
# 📁 apps/forgeiq-backend/main.py (Refined Code Gen)
# ===================================================
import os
import json
import datetime
import uuid
import logging
import asyncio
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, HTTPException, Body, Depends
# from starlette.responses import JSONResponse # Keep if you add custom global error handlers

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

# Core service imports (as before)
from core.event_bus.redis_bus import EventBus
from core.message_router import MessageRouter # Assuming MessageRouteNotFoundError, InvalidMessagePayloadError are exported from its __init__
from core.shared_memory import SharedMemoryStore
# ... other core imports ...

from .api_models import ( # Ensure all Pydantic models are imported
    PipelineGenerateRequest, PipelineGenerateResponse,
    DeploymentTriggerRequest, DeploymentTriggerResponse,
    SDKDagExecutionStatusModel, SDKDeploymentStatusModel,
    ProjectConfigResponse, BuildGraphResponse, TaskListResponse, TaskDefinitionModel,
    CodeGenerationRequest, CodeGenerationResponse, GeneratedCodeOutput, CodeGenerationPrompt # These are key
)

# --- LLM Client Initialization for "Codex" Code Generation ---
# This uses the OpenAI library, targeting models capable of code generation.
CODE_GENERATION_LLM_API_KEY = os.getenv("CODE_GENERATION_LLM_API_KEY", os.getenv("OPENAI_API_KEY")) # Allow specific or general OpenAI key
CODE_GENERATION_LLM_MODEL_NAME = os.getenv("CODE_GENERATION_LLM_MODEL_NAME", "gpt-4o") # Default to a strong code model
CODE_GENERATION_LLM_API_BASE_URL = os.getenv("CODE_GENERATION_LLM_API_BASE_URL") # For proxies or Azure OpenAI

llm_code_client = None # This is our "Codex" interaction client
if CODE_GENERATION_LLM_API_KEY:
    try:
        from openai import AsyncOpenAI # Ensure 'openai' is in root requirements.txt
        client_args = {"api_key": CODE_GENERATION_LLM_API_KEY}
        if CODE_GENERATION_LLM_API_BASE_URL:
            client_args["base_url"] = CODE_GENERATION_LLM_API_BASE_URL
        llm_code_client = AsyncOpenAI(**client_args)
        logger.info(f"Codex/Code Generation LLM client initialized. Target Model: {CODE_GENERATION_LLM_MODEL_NAME}.")
    except ImportError:
        logger.error("OpenAI Python client not installed. Code generation via API will fail. Please add 'openai' to requirements.txt")
    except Exception as e:
        logger.error(f"Failed to initialize Codex/Code Generation LLM client: {e}")
else:
    logger.warning("CODE_GENERATION_LLM_API_KEY not set. Code generation endpoint will be non-functional.")
# --- End LLM Client Initialization ---

# Initialize other core services (EventBus, MessageRouter, SharedMemoryStore) as before
event_bus_instance = EventBus()
message_router_instance = MessageRouter(event_bus_instance)
shared_memory_instance = SharedMemoryStore()

app = FastAPI(
    title="ForgeIQ Backend API (Python with Codex)",
    description="API Gateway for ForgeIQ, featuring LLM-powered code generation.",
    version="2.0.3" 
)
# ... (FastAPI OTel Instrumentation as before) ...
if _tracer:
    try: FastAPIInstrumentor.instrument_app(app, tracer_provider=_tracer.provider if _tracer else None) #type: ignore
    except Exception as e_otel_fastapi: logger.error(f"Failed to instrument FastAPI: {e_otel_fastapi}")


# ... (Existing /api/health and other API endpoints as defined in response #70) ...
@app.get("/api/health", tags=["Health"], summary="Health check for ForgeIQ Backend")
async def health_check():
    # ... (from response #70) ...
    with _start_api_span_if_available("health_check"): #type: ignore
        logger.info("API /api/health invoked")
        redis_ok = False
        if event_bus_instance.redis_client: # Check one of the redis clients
            try:
                await asyncio.to_thread(event_bus_instance.redis_client.ping)
                redis_ok = True
            except Exception: redis_ok = False
        return {
            "service_name": SERVICE_NAME, "status": "healthy",
            "timestamp": datetime.datetime.utcnow().isoformat(timespec='milliseconds') + "Z",
            "codex_llm_client_status": "initialized" if llm_code_client else "not_initialized",
            "redis_connection_status": "connected" if redis_ok else "disconnected_or_error"
        }


# === "Codex" Code Generation API Endpoint ===
@app.post("/api/forgeiq/code/generate", 
          response_model=CodeGenerationResponse, 
          tags=["Codex (Code Generation)"],
          summary="Generate code based on a prompt using a configured LLM (Codex-capable).")
async def codex_generate_code_endpoint(request_data: CodeGenerationRequest):
    # Use a more descriptive span name reflecting "Codex"
    span_attrs = {"codex.request_id": request_data.request_id, "codex.project_id": request_data.prompt_details.project_id}
    # Ensure _start_trace_span_if_available is defined in this file or imported correctly
    span_context_manager = _start_trace_span_if_available("codex_generate_code", **span_attrs) #type: ignore

    try:
        with span_context_manager as span: #type: ignore
            logger.info(f"API /code/generate (Codex): ReqID '{request_data.request_id}', Lang: {request_data.prompt_details.language}")

            if not llm_code_client:
                logger.error("Codex LLM client not initialized. Cannot process code generation request.")
                if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Codex LLM client not initialized"))
                raise HTTPException(status_code=503, detail="Code generation service (Codex) not available.")

            prompt_details = request_data.prompt_details

            # Construct messages for the ChatCompletion API (suited for models like gpt-3.5-turbo, gpt-4)
            # For older pure completion models (like code-davinci-002 if still accessible),
            # you would use `llm_code_client.completions.create` with a single `prompt` string.
            system_message = f"You are an expert code generation assistant, referred to as Codex. Your task is to generate clean, correct, and production-ready code. Target language: {prompt_details.language or 'not specified, infer if possible'}."
            if prompt_details.project_id:
                system_message += f" The code is for project context: {prompt_details.project_id}."

            user_messages_list = []
            user_messages_list.append({"role": "system", "content": system_message})
            if prompt_details.existing_code_context:
                user_messages_list.append({"role": "user", "content": f"Consider this existing code as context (you might be completing, refactoring, or using it as reference):\n```\n{prompt_details.existing_code_context}\n```\n\nNow, based on the following request, generate the code:"})
            user_messages_list.append({"role": "user", "content": prompt_details.prompt_text})

            try:
                logger.debug(f"Sending to Codex LLM (Model: {CODE_GENERATION_LLM_MODEL_NAME}). Max Tokens: {prompt_details.max_tokens}, Temp: {prompt_details.temperature}")
                if _tracer and span: span.set_attribute("llm.request.model", CODE_GENERATION_LLM_MODEL_NAME)

                completion = await llm_code_client.chat.completions.create(
                    model=CODE_GENERATION_LLM_MODEL_NAME,
                    messages=user_messages_list, #type: ignore
                    max_tokens=prompt_details.max_tokens,
                    temperature=prompt_details.temperature,
                    # Add other relevant parameters like 'stop' sequences if needed
                )

                generated_text = completion.choices[0].message.content if completion.choices and completion.choices[0].message else None
                finish_reason = completion.choices[0].finish_reason if completion.choices else "unknown"
                usage_data = completion.usage

                if _tracer and span:
                    span.set_attribute("llm.response.finish_reason", finish_reason or "")
                    if usage_data:
                        span.set_attribute("llm.usage.prompt_tokens", usage_data.prompt_tokens)
                        span.set_attribute("llm.usage.completion_tokens", usage_data.completion_tokens)
                        span.set_attribute("llm.usage.total_tokens", usage_data.total_tokens)

                if generated_text:
                    logger.info(f"Codex code generation successful for ReqID '{request_data.request_id}'. Finish: {finish_reason}")
                    # Attempt to clean up common LLM code block fences if present
                    if "```" in generated_text:
                        code_match = re.search(r"```(?:\w*\n)?(.*)```", generated_text, re.DOTALL | re.MULTILINE)
                        if code_match:
                            generated_text = code_match.group(1).strip()

                    output = GeneratedCodeOutput(
                        generated_code=generated_text,
                        language_detected=prompt_details.language, # Or try to infer from generated_text
                        model_used=CODE_GENERATION_LLM_MODEL_NAME, # Or completion.model
                        finish_reason=finish_reason,
                        usage_tokens=usage_data.model_dump() if usage_data else None #type: ignore
                    )
                    if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                    return CodeGenerationResponse(request_id=request_data.request_id, status="success", output=output)
                else:
                    err_msg = f"Codex LLM returned no content for ReqID '{request_data.request_id}'. Finish reason: {finish_reason}"
                    logger.error(err_msg)
                    if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "LLM no content"))
                    raise HTTPException(status_code=500, detail=err_msg)

            except Exception as e_llm:
                err_msg = f"Error during Codex LLM call for code generation (ReqID '{request_data.request_id}'): {e_llm}"
                logger.error(err_msg, exc_info=True)
                if _trace_api and span: span.record_exception(e_llm); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "LLM API call failed"))
                raise HTTPException(status_code=502, detail=f"Error communicating with Codex/code generation service: {str(e_llm)}")
    except HTTPException: # Re-raise HTTPExceptions directly
        raise
    except Exception as e_outer: # Catch any other unexpected error in the endpoint
        logger.error(f"Outer error in /code/generate endpoint for ReqID '{request_data.request_id}': {e_outer}", exc_info=True)
        if _trace_api and span: span.record_exception(e_outer); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "Outer endpoint error"))
        raise HTTPException(status_code=500, detail="Internal server error in code generation endpoint.")


# ... (Other existing API endpoints: /pipelines/generate, /dags/status, /deployments/trigger, etc. from response #70) ...
# ... (Ensure they also have the _start_api_span_if_available helper integrated if desired) ...
# ... (Ensure Uvicorn startup is handled by Dockerfile CMD) ...

# At the end of the file, before if __name__ == "__main__" if you have one for local uvicorn run
# you'd need to import `re` if the cleanup for generated_text uses it.
import re # For cleaning up LLM code output
