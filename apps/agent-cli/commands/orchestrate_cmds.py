# ====================================================
# 📁 apps/agent-cli/commands/orchestrate_cmds.py
# ====================================================
import typer
import asyncio # Typer uses this for async commands
import json    # For json.dumps
import logging
from typing import Optional, List, Dict, Any # For type hints

# For Typer <0.9 use typing_extensions for Annotated, otherwise typing for Python 3.9+
try: 
    from typing import Annotated 
except ImportError: 
    from typing_extensions import Annotated

from ..sdk_client_factory import get_sdk_client # Relative import

logger = logging.getLogger(__name__)
orchestrate_app = typer.Typer(name="orchestrate", help="Commands to trigger and manage orchestration flows.")

@orchestrate_app.command("generate-code")
async def generate_code_cmd( # Command function is async
    project_id: Annotated[str, typer.Option(help="Contextual Project ID for code generation.")],
    prompt_text: Annotated[str, typer.Option("--prompt", "-p", help="The detailed prompt for code generation.")],
    language: Annotated[Optional[str], typer.Option(help="Target programming language (e.g., 'python').")] = None,
    context_code: Annotated[Optional[str], typer.Option("--context-code", help="Existing code snippet for context.")] = None,
    max_tokens: Annotated[int, typer.Option(help="Max tokens for LLM generation.")] = 1024,
    temperature: Annotated[float, typer.Option(min=0.0, max=1.0, help="LLM temperature (0.0-1.0).")] = 0.2,
    request_id: Annotated[Optional[str], typer.Option(help="Custom request ID for tracking.")] = None
):
    """Generates code using ForgeIQ-backend (which calls the private AlgorithmAgent)."""
    typer.echo(f"Requesting code generation for project: {project_id} (Language: {language or 'any'})...")
    sdk_client = get_sdk_client()
    try:
        prompt_details_payload = {
            "prompt_text": prompt_text, "language": language,
            "project_id": project_id, "existing_code_context": context_code,
            "max_tokens": max_tokens, "temperature": temperature
        }
        # The SDK's generate_code_via_api expects a dictionary for prompt_details
        response = await sdk_client.generate_code_via_api( # Defined in response #83 for SDK
            prompt_details=prompt_details_payload, # type: ignore
            request_id=request_id
        )
        typer.echo("Code Generation Response:")
        typer.secho(json.dumps(response, indent=2), fg=typer.colors.GREEN)
    except Exception as e:
        logger.error(f"CLI error during 'generate-code': {e}", exc_info=True)
        typer.secho(f"ERROR generating code: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

@orchestrate_app.command("generate-pipeline")
async def generate_pipeline_cmd( # Command function is async
    project_id: Annotated[str, typer.Option(help="Project ID to generate pipeline for.")],
    prompt_text: Annotated[str, typer.Option("--prompt", "-p", help="User prompt for dynamic pipeline generation (BuildSurfAgent).")],
    request_id: Annotated[Optional[str], typer.Option(help="Custom request ID for tracking.")] = None
):
    """Generates a pipeline DAG using BuildSurfAgent via ForgeIQ-backend."""
    typer.echo(f"Requesting pipeline DAG generation for project: {project_id}...")
    sdk_client = get_sdk_client()
    try:
        response = await sdk_client.submit_pipeline_prompt( # Defined in response #67 for SDK
            project_id=project_id, user_prompt=prompt_text, request_id=request_id
        )
        typer.echo("Pipeline Generation Request Response:")
        typer.secho(json.dumps(response, indent=2), fg=typer.colors.GREEN)
    except Exception as e:
        logger.error(f"CLI error during 'generate-pipeline': {e}", exc_info=True)
        typer.secho(f"ERROR generating pipeline: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

@orchestrate_app.command("trigger-deployment")
async def trigger_deployment_cmd( # Command function is async
    project_id: Annotated[str, typer.Option(help="Project ID.")],
    service_name: Annotated[str, typer.Option(help="Service name to deploy.")],
    commit_sha: Annotated[str, typer.Option(help="Commit SHA to deploy.")],
    target_environment: Annotated[str, typer.Option(help="Target environment (e.g., 'staging', 'production').")],
    request_id: Annotated[Optional[str], typer.Option(help="Custom request ID for tracking.")] = None
):
    """Triggers a deployment for a service via ForgeIQ-backend."""
    typer.echo(f"Requesting deployment for service: {service_name}, project: {project_id}, commit: {commit_sha}, env: {target_environment}...")
    sdk_client = get_sdk_client()
    try:
        response = await sdk_client.trigger_deployment( # Defined in response #67 for SDK
            project_id=project_id, service_name=service_name,
            commit_sha=commit_sha, target_environment=target_environment,
            request_id=request_id
        )
        typer.echo("Deployment Trigger Response:")
        typer.secho(json.dumps(response, indent=2), fg=typer.colors.GREEN)
    except Exception as e:
        logger.error(f"CLI error during 'trigger-deployment': {e}", exc_info=True)
        typer.secho(f"ERROR triggering deployment: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
