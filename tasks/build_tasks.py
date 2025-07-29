# File: forgeiq-backend/tasks/build_tasks.py

import logging
import os
import asyncio
import subprocess
import json # Import json for passing config options
from typing import Dict, Any, List # Import List for history type
from fastapi import HTTPException
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

import httpx # For HTTP requests and error types
from sqlalchemy.orm import Session # For type hinting the db session
from app.database import SessionLocal # Import SessionLocal to get DB session in task

# === ForgeIQ's Celery App and Utilities ===
from forgeiq_celery import celery_app
from forgeiq_utils import update_forgeiq_task_state_and_notify # Make sure it accepts 'db' as first arg

# === ForgeIQ's internal modules ===
# No longer needed directly here as tasks are self-contained or call other tasks
# from app.orchestrator import Orchestrator
# from app.auth import get_private_intel_client
from api_models import UserPromptData # Input for /gateway and general LLM tasks
from api_models import CodeGenerationRequest, PipelineGenerateRequest, DeploymentTriggerRequest # Request models
from api_models import SDKMCPStrategyRequestContext, SDKMCPStrategyResponse # MCP models

# === Gemini Integration ===
import google.generativeai as genai

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- Configure Gemini API Key ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    logger.warning("GEMINI_API_KEY environment variable is NOT set. Gemini API calls will fail.")
else:
    genai.configure(api_key=GEMINI_API_KEY)
    logger.info("Gemini API configured successfully.")

# --- Retry Strategy for external HTTP calls (re-using from Orchestrator/Design API) ---
# Note: This retry strategy is for HTTPX calls, not for subprocess or genai directly.
# You might need to wrap genai calls or subprocess calls with custom retry logic if needed.
# For simplicity, we'll let direct API errors propagate for now for easier debugging.
HTTP_RETRY_STRATEGY = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    retry=(
        retry_if_exception_type(httpx.HTTPStatusError) |
        retry_if_exception_type(httpx.RequestError)
    ),
    before_sleep=before_sleep_log(logger, logging.INFO),
)


# === Celery Task for Generic LLM/Code Generation (handles both Codex CLI and Gemini API) ===
# This task will now accept a more general input like UserPromptData for flexibility
@celery_app.task(bind=True)
async def run_codex_generation_task(self, request_payload_dict: Dict[str, Any], forgeiq_task_id: str):
    """
    Generates code or general text using either Codex CLI or Gemini API based on request_payload_dict.
    This task is called by /gateway for general chat and by /build for code generation steps.
    """
    db: Session = SessionLocal() # Acquire a new database session for this task
    try:
        # Determine if it's a CodeGenerationRequest or a general UserPromptData
        is_code_request = "prompt_text" in request_payload_dict and "config_options" in request_payload_dict
        
        # Adapt input parsing based on source
        if is_code_request:
            request_data = CodeGenerationRequest(**request_payload_dict)
            prompt_text = request_data.prompt_text
            config_options = request_data.config_options
            history_for_llm: List[Dict[str, str]] = [] # Code generation requests might not have history
            task_log_context = f"code gen for project '{request_data.project_id}'"
        else:
            request_data = UserPromptData(**request_payload_dict) # From /gateway
            prompt_text = request_data.prompt
            history_for_llm = request_data.history
            config_options = request_data.context_data.get("config_options", {}) # Extract from context_data
            task_log_context = f"general LLM for prompt '{prompt_text[:50]}...'"

        llm_provider = config_options.get("llm_provider", "gemini").lower() # Default to Gemini

        update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="processing", logs=f"Starting LLM generation using {llm_provider}...",
            current_stage=f"{llm_provider.capitalize()} Gen", progress=10
        )
        logger.info(f"ForgeIQ Task {forgeiq_task_id}: Processing {task_log_context} using {llm_provider}.")

        generated_content = ""

        if llm_provider == "codex":
            if not os.path.exists("/usr/local/bin/codex-cli"): # Basic check for CLI tool
                error_msg = "Codex CLI not found in worker environment. Cannot use Codex provider."
                raise ValueError(error_msg)
            
            logger.info(f"Task {forgeiq_task_id}: Using Codex CLI for '{prompt_text[:50]}'")
            try:
                def _blocking_codex_cli_call():
                    cmd = [
                        "codex-cli",
                        "--prompt", prompt_text,
                        "--config", json.dumps(config_options) # Pass config options to CLI
                    ]
                    # Note: check=True raises CalledProcessError for non-zero exit codes
                    return subprocess.run(cmd, capture_output=True, text=True, check=True)

                process = await asyncio.to_thread(_blocking_codex_cli_call)
                generated_content = process.stdout.strip()
                logger.info(f"Task {forgeiq_task_id}: Codex CLI call successful.")

            except subprocess.CalledProcessError as e:
                error_detail = f"Codex CLI failed: {e.stderr.strip() or e.stdout.strip()}"
                logger.error(f"Task {forgeiq_task_id}: {error_detail}")
                raise ValueError(error_detail) from e # Raise ValueError to be caught below
            except Exception as e:
                error_detail = f"An unexpected error occurred during Codex CLI generation: {str(e)}"
                logger.exception(f"Task {forgeiq_task_id}: {error_detail}")
                raise ValueError(error_detail) from e # Re-raise for error handling

        elif llm_provider == "gemini":
            if not GEMINI_API_KEY:
                error_msg = "GEMINI_API_KEY not set. Cannot use Gemini provider."
                raise ValueError(error_msg)

            logger.info(f"Task {forgeiq_task_id}: Using Gemini API for '{prompt_text[:50]}'")
            try:
                gemini_messages = []
                for msg in history_for_llm:
                    role = "user" if msg["role"] == "user" else "model"
                    gemini_messages.append({"role": role, "parts": [msg["content"]]})
                gemini_messages.append({"role": "user", "parts": [prompt_text]})

                model = genai.GenerativeModel('gemini-pro') # Or other model like 'gemini-1.5-pro-latest'

                response = model.generate_content(
                    gemini_messages,
                    generation_config=genai.GenerationConfig(
                        temperature=0.7,
                        max_output_tokens=1000,
                    ),
                    safety_settings=[
                        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                    ]
                )
                generated_content = response.text
                logger.info(f"Task {forgeiq_task_id}: Gemini API call successful.")

            except Exception as e:
                error_detail = f"Gemini API call failed: {str(e)}"
                logger.error(f"Task {forgeiq_task_id}: {error_detail}")
                raise ValueError(error_detail) from e

        else:
            error_detail = f"Unsupported LLM provider: {llm_provider}. Must be 'codex' or 'gemini'."
            logger.error(f"Task {forgeiq_task_id}: {error_detail}")
            raise ValueError(error_detail)

        # Final success update
        update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="completed", logs=f"LLM generation successful using {llm_provider}.",
            current_stage=f"{llm_provider.capitalize()} Gen", progress=100,
            output_data={"llm_response": generated_content} # Consistent output key for frontend
        )
        logger.info(f"ForgeIQ Task {forgeiq_task_id}: LLM generation completed successfully.")
        return {"status": "success", "llm_response": generated_content}

    except ValueError as e: # Catch ValueErrors raised for specific known failures (e.g., config, API key, CLI issues)
        error_detail = f"LLM generation failed: {str(e)}"
        logger.error(f"ForgeIQ Task {forgeiq_task_id}: {error_detail}")
        update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="error", logs=error_detail,
            current_stage=f"LLM Gen Failure", progress=0, details={"error": error_detail}
        )
        raise # Re-raise for Celery to mark as failed
    except Exception as e:
        error_detail = f"An unexpected error occurred during LLM generation: {str(e)}"
        logger.exception(f"ForgeIQ Task {forgeiq_task_id}: {error_detail}")
        update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="error", logs=error_detail,
            current_stage=f"LLM Gen Unhandled Error", progress=0, details={"error": error_detail}
        )
        raise # Re-raise for Celery to mark as failed
    finally:
        db.close() # Ensure database session is closed


# === Celery Task for Overall ForgeIQ Pipeline Orchestration (POST /build maps to this) ===
@celery_app.task(bind=True)
async def run_forgeiq_pipeline_task(self, task_payload_dict: Dict[str, Any], forgeiq_task_id: str):
    """
    Orchestrates the full ForgeIQ pipeline, including code generation, MCP, and algorithm application.
    """
    db: Session = SessionLocal() # Acquire a new database session for this task
    try:
        task_payload_from_orchestrator = task_payload_dict

        update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="running", logs="ForgeIQ pipeline initiated.",
            current_stage="Pipeline Init", progress=10
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id} started for project {task_payload_from_orchestrator.get('project_id')}")

        # --- 1. Code Generation (e.g., using run_codex_generation_task for actual LLM call) ---
        await update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, logs="Triggering code generation...",
            current_stage="Code Gen", progress=20
        )
        
        # Prepare input for run_codex_generation_task. It now expects UserPromptData-like structure
        # with optional config_options for provider selection.
        code_gen_request_payload = {
            "prompt": task_payload_from_orchestrator.get('payload', {}).get('workflowGoal', 'Generate basic application code.'),
            "history": [], # Provide an empty history if not relevant for pipeline code gen
            "context_data": {
                "config_options": {"llm_provider": "gemini"} # Explicitly use Gemini for pipeline step, or "codex"
            }
        }

        codex_gen_result = await run_codex_generation_task.delay(
            code_gen_request_payload, # Pass the adjusted payload
            forgeiq_task_id # Note: Passing the *same* forgeiq_task_id for sub-tasks is unusual,
                            # often sub-tasks get their own IDs. For progress tracking, this works.
        ).get(timeout=3600) # .get() here blocks the pipeline task until sub-task completes.

        if codex_gen_result.get("status") != "success":
            raise ValueError(f"Code generation failed: {codex_gen_result.get('error', 'Unknown error')}")

        generated_code = codex_gen_result.get("llm_response") # Get the llm_response key now
        await update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, logs="Code generation completed.",
            current_stage="Code Gen", progress=30, output_data={"generated_code_summary": generated_code[:100] + "..."}
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id}: Code generation successful.")

        # --- 2. MCP Strategy Optimization (Simulated or Real) ---
        await update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, logs="Requesting MCP strategy optimization...",
            current_stage="MCP Optimization", progress=50
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id}: Requesting MCP strategy.")

        # You would integrate your actual MCP logic here. Simulating for now.
        # intel_stack_client = await get_private_intel_client()
        # mcp_orchestrator = Orchestrator(forgeiq_sdk_client=intel_stack_client, message_router=None)
        # optimized_dag = await mcp_orchestrator.request_and_apply_mcp_optimization(
        #     project_id=task_payload_from_orchestrator.get('project_id'),
        #     current_dag=None,
        #     flow_id=forgeiq_task_id
        # )
        # if not optimized_dag:
        #     raise ValueError("MCP strategy optimization failed or returned no DAG.")
        # simulated_optimized_dag_id = optimized_dag.dag_id # Use actual result
        simulated_optimized_dag_id = "mock_dag_123" # Mock for now
        
        await update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, logs="MCP strategy optimization complete.",
            current_stage="MCP Optimization", progress=70, output_data={"optimized_dag_id": simulated_optimized_dag_id}
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id}: MCP strategy successful.")

        # --- 3. Apply Proprietary Algorithms (Simulated or Real) ---
        await update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, logs="Applying proprietary algorithms...",
            current_stage="Algorithm Application", progress=80
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id}: Applying proprietary algorithms.")

        # Simulate algorithm application result
        applied_algo_result = {"status": "success", "processed_data": "some_optimized_data"}

        await update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, logs="Proprietary algorithms applied.",
            current_stage="Algorithm Application", progress=90, output_data={"algorithm_result": applied_algo_result}
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id}: Proprietary algorithms applied.")

        # Final success update
        await update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="completed", logs="ForgeIQ pipeline completed successfully.",
            current_stage="Pipeline Complete", progress=100, output_data={"final_build_output": "URL to build artifact / generated files"}
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id}: Pipeline finished successfully.")

        return {"status": "success", "message": "ForgeIQ pipeline executed successfully."}

    except ValueError as e: # Catch explicit ValueErrors
        error_detail = str(e)
        logger.error(f"ForgeIQ Pipeline Task {forgeiq_task_id} failed with controlled error: {error_detail}")
        update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="error", logs=error_detail,
            current_stage="Pipeline Failure", progress=0, details={"error": error_detail}
        )
        raise # Re-raise for Celery to mark as failed
    except Exception as e:
        error_detail = f"An unexpected error occurred during ForgeIQ pipeline execution: {str(e)}"
        logger.exception(f"ForgeIQ Pipeline Task {forgeiq_task_id}: {error_detail}")
        update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="error", logs=error_detail,
            current_stage="Pipeline Unhandled Error", progress=0, details={"error": error_detail}
        )
        raise # Re-raise for Celery to mark as failed
    finally:
        db.close() # Ensure database session is closed


# === Celery Task for Pipeline Generation endpoint (Simulated) ===
@celery_app.task(bind=True)
async def run_pipeline_generation_task(self, request_payload_dict: Dict[str, Any], forgeiq_task_id: str):
    db: Session = SessionLocal() # Acquire a new database session for this task
    try:
        request = PipelineGenerateRequest(**request_payload_dict)
        await update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="running", logs="Simulating pipeline generation...",
            current_stage="Pipeline Gen", progress=50
        )
        await asyncio.sleep(5) # Simulate work
        await update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="completed", logs="Pipeline generation simulated successfully.",
            current_stage="Pipeline Gen", progress=100, output_data={"pipeline_id": "simulated_pipeline_123"}
        )
        return {"status": "success", "pipeline_id": "simulated_pipeline_123"}
    except Exception as e:
        error_detail = f"Error simulating pipeline generation: {str(e)}"
        logger.exception(f"ForgeIQ Pipeline Gen Task {forgeiq_task_id}: {error_detail}")
        update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="error", logs=error_detail,
            current_stage="Pipeline Gen Error", progress=0, details={"error": error_detail}
        )
        raise
    finally:
        db.close()


# === Celery Task for Deployment Trigger endpoint (Simulated) ===
@celery_app.task(bind=True)
async def run_deployment_trigger_task(self, request_payload_dict: Dict[str, Any], forgeiq_task_id: str):
    db: Session = SessionLocal() # Acquire a new database session for this task
    try:
        request = DeploymentTriggerRequest(**request_payload_dict)
        await update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="running", logs=f"Simulating deployment for {request.service_name}...",
            current_stage="Deployment Trigger", progress=50
        )
        await asyncio.sleep(10) # Simulate work
        await update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="completed", logs=f"Deployment for {request.service_name} simulated successfully.",
            current_stage="Deployment Trigger", progress=100, output_data={"deployment_status": "deployed"}
        )
        return {"status": "success", "service_name": request.service_name, "deployment_status": "deployed"}
    except Exception as e:
        error_detail = f"Error simulating deployment trigger: {str(e)}"
        logger.exception(f"ForgeIQ Deployment Task {forgeiq_task_id}: {error_detail}")
        update_forgeiq_task_state_and_notify(
            db, # Pass DB session
            forgeiq_task_id, status="error", logs=error_detail,
            current_stage="Deployment Trigger Error", progress=0, details={"error": error_detail}
        )
        raise
    finally:
        db.close()
