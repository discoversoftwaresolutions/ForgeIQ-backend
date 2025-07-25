# File: forgeiq-backend/tasks/build_tasks.py

import logging
import os
import asyncio
import subprocess
from typing import Dict, Any
from fastapi import HTTPException # For raising structured errors from tasks
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

import httpx # <--- ADDED: Import httpx for HTTP requests and error types

# === ForgeIQ's Celery App and Utilities ===
from forgeiq_celery import celery_app # ForgeIQ's own Celery app
from forgeiq_utils import update_forgeiq_task_state_and_notify # ForgeIQ's own state update utility

# === ForgeIQ's internal modules ===
from app.orchestrator import Orchestrator # Orchestrator is now in app/orchestrator.py
from app.auth import get_private_intel_client # For getting httpx client for intel stack
from api_models import CodeGenerationRequest, PipelineGenerateRequest, DeploymentTriggerRequest # Request models
from api_models import SDKMCPStrategyRequestContext, SDKMCPStrategyResponse # MCP models

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- Retry Strategy for external HTTP calls (re-using from Orchestrator/Design API) ---
HTTP_RETRY_STRATEGY = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    retry=(
        retry_if_exception_type(httpx.HTTPStatusError) |
        retry_if_exception_type(httpx.RequestError)
    ),
    retry_error_codes={429, 500, 502, 503, 504},
    before_sleep=before_sleep_log(logger, logging.INFO),
)


# === Celery Task for Codex Code Generation ===
@celery_app.task(bind=True)
async def run_codex_generation_task(self, request_payload_dict: Dict[str, Any], forgeiq_task_id: str):
    request = CodeGenerationRequest(**request_payload_dict)

    await update_forgeiq_task_state_and_notify(
        forgeiq_task_id, status="running", logs="Starting Codex code generation...",
        current_stage="Codex Gen", progress=10
    )
    logger.info(f"ForgeIQ Task {forgeiq_task_id}: Processing code generation for project '{request.project_id}'")

    try:
        def _blocking_codex_cli_call():
            cmd = [
                "codex-cli",
                "--prompt", request.prompt_text,
                "--config", json.dumps(request.config_options)
            ]
            return subprocess.run(cmd, capture_output=True, text=True, check=True)

        process = await asyncio.to_thread(_blocking_codex_cli_call)
        generated_code = process.stdout.strip()

        await update_forgeiq_task_state_and_notify(
            forgeiq_task_id, status="completed", logs="Codex generation successful.",
            current_stage="Codex Gen", progress=100, output_data={"generated_code": generated_code}
        )
        logger.info(f"ForgeIQ Task {forgeiq_task_id}: Codex generation completed.")
        return {"status": "success", "generated_code": generated_code}

    except subprocess.CalledProcessError as e:
        error_detail = f"Codex CLI failed: {e.stderr.strip() or e.stdout.strip()}"
        logger.error(f"ForgeIQ Task {forgeiq_task_id}: {error_detail}")
        await update_forgeiq_task_state_and_notify(
            forgeiq_task_id, status="failed", logs=error_detail,
            current_stage="Codex Gen Failure", progress=0, details={"error": error_detail}
        )
        raise HTTPException(status_code=500, detail=error_detail) from e
    except Exception as e:
        error_detail = f"An unexpected error occurred during Codex generation: {str(e)}"
        logger.exception(f"ForgeIQ Task {forgeiq_task_id}: {error_detail}")
        await update_forgeiq_task_state_and_notify(
            forgeiq_task_id, status="failed", logs=error_detail,
            current_stage="Codex Gen Unhandled Error", progress=0, details={"error": error_detail}
        )
        raise HTTPException(status_code=500, detail=error_detail) from e


# === Celery Task for Overall ForgeIQ Pipeline Orchestration (POST /build maps to this) ===
@celery_app.task(bind=True)
async def run_forgeiq_pipeline_task(self, task_payload_dict: Dict[str, Any], forgeiq_task_id: str):
    task_payload_from_orchestrator = task_payload_dict

    await update_forgeiq_task_state_and_notify(
        forgeiq_task_id, status="running", logs="ForgeIQ pipeline initiated.",
        current_stage="Pipeline Init", progress=10
    )
    logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id} started for project {task_payload_from_orchestrator.get('project_id')}")

    try:
        # --- 1. Code Generation (e.g., using Codex CLI) ---
        await update_forgeiq_task_state_and_notify(
            forgeiq_task_id, logs="Triggering Codex code generation...",
            current_stage="Codex Gen", progress=20
        )
        codex_gen_result = await run_codex_generation_task.delay(
            {"project_id": task_payload_from_orchestrator.get('project_id'), "prompt_text": task_payload_from_orchestrator.get('payload', {}).get('prompt')},
            forgeiq_task_id
        ).get(timeout=3600)
        
        if codex_gen_result.get("status") != "success":
            raise HTTPException(status_code=500, detail=f"Codex generation failed: {codex_gen_result.get('error')}")

        generated_code = codex_gen_result.get("generated_code")
        await update_forgeiq_task_state_and_notify(
            forgeiq_task_id, logs="Codex generation completed.",
            current_stage="Codex Gen", progress=30, output_data={"generated_code_summary": generated_code[:100] + "..."}
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id}: Codex generation successful.")

        # --- 2. MCP Strategy Optimization ---
        await update_forgeiq_task_state_and_notify(
            forgeiq_task_id, logs="Requesting MCP strategy optimization...",
            current_stage="MCP Optimization", progress=50
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id}: Requesting MCP strategy.")

        intel_stack_client = await get_private_intel_client()
        mcp_orchestrator = Orchestrator(forgeiq_sdk_client=intel_stack_client, message_router=None)

        optimized_dag = await mcp_orchestrator.request_and_apply_mcp_optimization(
            project_id=task_payload_from_orchestrator.get('project_id'),
            current_dag=None, # You would provide an actual current DAG here
            flow_id=forgeiq_task_id
        )

        if not optimized_dag:
            raise HTTPException(status_code=500, detail="MCP strategy optimization failed or returned no DAG.")
        
        await update_forgeiq_task_state_and_notify(
            forgeiq_task_id, logs="MCP strategy optimization complete.",
            current_stage="MCP Optimization", progress=70, output_data={"optimized_dag_id": optimized_dag.dag_id}
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id}: MCP strategy successful.")

        # --- 3. Apply Proprietary Algorithms ---
        await update_forgeiq_task_state_and_notify(
            forgeiq_task_id, logs="Applying proprietary algorithms...",
            current_stage="Algorithm Application", progress=80
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id}: Applying proprietary algorithms.")

        # Simulate algorithm application result
        applied_algo_result = {"status": "success", "processed_data": "some_optimized_data"}

        await update_forgeiq_task_state_and_notify(
            forgeiq_task_id, logs="Proprietary algorithms applied.",
            current_stage="Algorithm Application", progress=90, output_data={"algorithm_result": applied_algo_result}
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id}: Proprietary algorithms applied.")

        # Final success update
        await update_forgeiq_task_state_and_notify(
            forgeiq_task_id, status="completed", logs="ForgeIQ pipeline completed successfully.",
            current_stage="Pipeline Complete", progress=100, output_data={"final_build_output": "URL to build artifact / generated files"}
        )
        logger.info(f"ForgeIQ Pipeline Task {forgeiq_task_id}: Pipeline finished successfully.")

        return {"status": "success", "message": "ForgeIQ pipeline executed successfully."}

    except HTTPException as e:
        error_detail = e.detail
        status_code = e.status_code
        logger.error(f"ForgeIQ Pipeline Task {forgeiq_task_id} failed with HTTP exception: {error_detail}")
        await update_forgeiq_task_state_and_notify(
            forgeiq_task_id, status="failed", logs=error_detail,
            current_stage="Pipeline Failure", progress=0, details={"error": error_detail, "status_code": status_code}
        )
        raise # Re-raise for Celery to mark as failed
    except Exception as e:
        error_detail = f"An unexpected error occurred during ForgeIQ pipeline execution: {str(e)}"
        logger.exception(f"ForgeIQ Pipeline Task {forgeiq_task_id}: {error_detail}")
        await update_forgeiq_task_state_and_notify(
            forgeiq_task_id, status="failed", logs=error_detail,
            current_stage="Pipeline Unhandled Error", progress=0, details={"error": error_detail}
        )
        raise # Re-raise for Celery to mark as failed


# === Celery Task for Pipeline Generation endpoint ===
@celery_app.task(bind=True)
async def run_pipeline_generation_task(self, request_payload_dict: Dict[str, Any], forgeiq_task_id: str):
    request = PipelineGenerateRequest(**request_payload_dict)
    # This task would call your internal pipeline generation logic
    # For now, just simulate
    await update_forgeiq_task_state_and_notify(
        forgeiq_task_id, status="running", logs="Simulating pipeline generation...",
        current_stage="Pipeline Gen", progress=50
    )
    await asyncio.sleep(5) # Simulate work
    await update_forgeiq_task_state_and_notify(
        forgeiq_task_id, status="completed", logs="Pipeline generation simulated successfully.",
        current_stage="Pipeline Gen", progress=100, output_data={"pipeline_id": "simulated_pipeline_123"}
    )
    return {"status": "success", "pipeline_id": "simulated_pipeline_123"}

# === Celery Task for Deployment Trigger endpoint ===
@celery_app.task(bind=True)
async def run_deployment_trigger_task(self, request_payload_dict: Dict[str, Any], forgeiq_task_id: str):
    request = DeploymentTriggerRequest(**request_payload_dict)
    # This task would call your internal deployment logic
    # For now, just simulate
    await update_forgeiq_task_state_and_notify(
        forgeiq_task_id, status="running", logs=f"Simulating deployment for {request.service_name}...",
        current_stage="Deployment Trigger", progress=50
    )
    await asyncio.sleep(10) # Simulate work
    await update_forgeiq_task_state_and_notify(
        forgeiq_task_id, status="completed", logs=f"Deployment for {request.service_name} simulated successfully.",
        current_stage="Deployment Trigger", progress=100, output_data={"deployment_status": "deployed"}
    )
    return {"status": "success", "service_name": request.service_name, "deployment_status": "deployed"}
