# ===================================================
# 📁 apps/forgeiq-backend/main.py (V0.3 Enhanced with Code Gen)
# ===================================================
# ... (existing imports: os, json, datetime, uuid, logging, asyncio, FastAPI, HTTPException, etc.) ...
from typing import Dict, Any, Optional, List # Ensure List is imported

# --- Observability Setup (as before) ---
# ... (SERVICE_NAME, LOG_LEVEL, logger, _tracer, _trace_api, setup_tracing as before) ...
# --- End Observability Setup ---

# Core service imports (as before)
# from core.event_bus.redis_bus import EventBus
# from core.message_router import MessageRouter, MessageRouteNotFoundError, InvalidMessagePayloadError
# from core.shared_memory import SharedMemoryStore
# from core.build_system_config import ...
# from core.build_graph import ...
# from core.task_runner import ...

from .api_models import ( # Ensure all Pydantic models are imported
    PipelineGenerateRequest, PipelineGenerateResponse,
    DeploymentTriggerRequest, DeploymentTriggerResponse,
    SDKDagExecutionStatusModel, SDKDeploymentStatusModel,
    ProjectConfigResponse, BuildGraphResponse, TaskListResponse, TaskDefinitionModel,
    CodeGenerationRequest, CodeGenerationResponse, GeneratedCodeOutput, CodeGenerationPrompt # <<< NEW MODELS
)

# --- LLM Client Initialization (for Code Generation) ---
CODE_GEN_LLM_API_KEY = os.getenv("CODE_GENERATION_LLM_API_KEY", os.getenv("LLM_API_KEY"))
CODE_GEN_LLM_MODEL_NAME = os.getenv("CODE_GENERATION_LLM_MODEL_NAME", "gpt-3.5-turbo-instruct") # Example
CODE_GEN_LLM_API_BASE_URL = os.getenv("CODE_GENERATION_LLM_API_BASE_URL")

llm_code_client = None
if CODE_GEN_LLM_API_KEY:
    try:
        from openai import AsyncOpenAI # Add 'openai' to root requirements.txt
        client_args = {"api_key": CODE_GEN_LLM_API_KEY}
        if CODE_GEN_LLM_API_BASE_URL:
            client_args["base_url"] = CODE_GEN_LLM_API_BASE_URL
        llm_code_client = AsyncOpenAI(**client_args)
        logger.info(f"Code Generation LLM client initialized (Model: {CODE_GEN_LLM_MODEL_NAME}).")
    except ImportError:
        logger.error("OpenAI Python client not installed for Code Generation. Please add 'openai' to requirements.txt")
    except Exception as e:
        logger.error(f"Failed to initialize Code Generation LLM client: {e}")
else:
    logger.warning("CODE_GENERATION_LLM_API_KEY not set. Code generation endpoint will not function.")
# --- End LLM Client Initialization ---

# Initialize other core services (EventBus, MessageRouter, SharedMemoryStore) as before
# ... (event_bus_instance, message_router_instance, shared_memory_instance) ...

app = FastAPI(
    title="ForgeIQ Backend API (Python)",
    description="API Gateway for the ForgeIQ Agentic Build System, including Code Generation.",
    version="2.0.2" # Incremented version
)
# ... (FastAPI OTel Instrumentation as before) ...

# ... (Existing /api/health and other endpoints as defined in response #70) ...


# === NEW Code Generation API Endpoint ===
@app.post("/api/forgeiq/code/generate", 
          response_model=CodeGenerationResponse, 
          tags=["Code Generation"],
          summary="Generate code based on a prompt using an LLM.")
async def generate_code_endpoint(request_data: CodeGenerationRequest):
    span_attrs = {"code_gen.request_id": request_data.request_id, "code_gen.project_id": request_data.prompt_details.project_id}
    with _start_api_span("generate_code", **span_attrs) as span: # type: ignore
        logger.info(f"API /code/generate: ReqID '{request_data.request_id}', Lang: {request_data.prompt_details.language}")

        if not llm_code_client:
            logger.error("Code generation LLM client not initialized. Cannot process request.")
            if _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "LLM client not initialized"))
            raise HTTPException(status_code=503, detail="Code generation service not available.")

        prompt_details = request_data.prompt_details

        # Construct a more detailed system prompt if needed, or use user prompt directly
        # For code generation, providing clear instructions, desired language, and context is key.
        messages_for_llm = [
            {"role": "system", "content": f"You are an expert code generation assistant. Generate clean, correct, and production-ready code in {prompt_details.language or 'the inferred language'}. Adhere to common best practices for that language. Only output the code itself, no explanations unless explicitly asked in the user prompt."},
            {"role": "user", "content": prompt_details.prompt_text}
        ]
        if prompt_details.existing_code_context:
             messages_for_llm.insert(1, {"role": "user", "content": f"Here is some existing code for context or to complete/refactor:\n```\n{prompt_details.existing_code_context}\n```\n"})


        try:
            logger.debug(f"Sending to LLM for code generation. Model: {CODE_GEN_LLM_MODEL_NAME}. Max Tokens: {prompt_details.max_tokens}")
            completion = await llm_code_client.chat.completions.create(
                model=CODE_GEN_LLM_MODEL_NAME,
                messages=messages_for_llm, #type: ignore # Allow a list of message dicts
                max_tokens=prompt_details.max_tokens,
                temperature=prompt_details.temperature,
                # stop=["\n\n\n"] # Example stop sequence
            )

            generated_text = completion.choices[0].message.content if completion.choices else None
            finish_reason = completion.choices[0].finish_reason if completion.choices else "unknown"
            usage_data = completion.usage # Pydantic model for usage

            if _tracer and span:
                span.set_attribute("llm.model_used", CODE_GEN_LLM_MODEL_NAME)
                span.set_attribute("llm.finish_reason", finish_reason or "")
                if usage_data:
                    span.set_attribute("llm.usage.prompt_tokens", usage_data.prompt_tokens)
                    span.set_attribute("llm.usage.completion_tokens", usage_data.completion_tokens)
                    span.set_attribute("llm.usage.total_tokens", usage_data.total_tokens)


            if generated_text:
                logger.info(f"Code generation successful for ReqID '{request_data.request_id}'. Finish reason: {finish_reason}")
                output = GeneratedCodeOutput(
                    generated_code=generated_text,
                    language_detected=prompt_details.language, # Could be enhanced with actual detection
                    model_used=CODE_GEN_LLM_MODEL_NAME,
                    finish_reason=finish_reason,
                    usage_tokens=usage_data.model_dump() if usage_data else None #type: ignore
                )
                if _tracer and _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.OK))
                return CodeGenerationResponse(request_id=request_data.request_id, status="success", output=output)
            else:
                logger.error(f"LLM returned no content for ReqID '{request_data.request_id}'. Finish reason: {finish_reason}")
                if _tracer and _trace_api and span: span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "LLM no content"))
                raise HTTPException(status_code=500, detail="LLM returned no content.")

        except Exception as e:
            logger.error(f"Error during LLM call for code generation (ReqID '{request_data.request_id}'): {e}", exc_info=True)
            if _tracer and _trace_api and span: span.record_exception(e); span.set_status(_trace_api.Status(_trace_api.StatusCode.ERROR, "LLM API call failed"))
            raise HTTPException(status_code=502, detail=f"Error communicating with code generation service: {str(e)}")

# ... (Uvicorn startup is handled by Dockerfile CMD)
