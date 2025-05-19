# ================================================
# 📁 app/agent-cli/commands/status_cmds.py
# ================================================
import typer
import asyncio # Typer uses this for async commands
import json 
import logging
from typing import Optional # For type hints
try: from typing import Annotated 
except ImportError: from typing_extensions import Annotated

from ..sdk_client_factory import get_sdk_client

logger = logging.getLogger(__name__)
status_app = typer.Typer(name="status", help="Commands to query status of various system components.")

@status_app.command("dag")
async def get_dag_status_cmd( # Command function is async
    project_id: Annotated[str, typer.Option(help="Project ID for the DAG.")],
    dag_id: Annotated[str, typer.Option(help="The ID of the DAG to query.")]
):
    """Gets the execution status of a specific DAG."""
    typer.echo(f"Querying status for DAG '{dag_id}' in project '{project_id}'...")
    sdk_client = get_sdk_client()
    try:
        status_data = await sdk_client.get_dag_execution_status(project_id=project_id, dag_id=dag_id) # Defined in response #67
        typer.echo("DAG Execution Status:")
        typer.secho(json.dumps(status_data, indent=2), fg=typer.colors.GREEN)
    except Exception as e:
        logger.error(f"CLI error getting DAG status for '{dag_id}': {e}", exc_info=True)
        typer.secho(f"Error getting DAG status: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

@status_app.command("deployment")
async def get_deployment_status_cmd( # Command function is async
    project_id: Annotated[str, typer.Option(help="Project ID.")],
    service_name: Annotated[str, typer.Option(help="Service name on Railway.")],
    request_id: Annotated[str, typer.Option(help="The deployment request ID to query.")]
):
    """Gets the status of a specific deployment request."""
    typer.echo(f"Querying deployment status for request ID '{request_id}' (service: {service_name}, project: {project_id})...")
    sdk_client = get_sdk_client()
    try:
        status_data = await sdk_client.get_deployment_status( # Defined in response #67
            project_id=project_id, 
            service_name=service_name, 
            deployment_request_id=request_id
        )
        typer.echo("Deployment Status:")
        typer.secho(json.dumps(status_data, indent=2), fg=typer.colors.GREEN)
    except Exception as e:
        logger.error(f"CLI error getting deployment status for request '{request_id}': {e}", exc_info=True)
        typer.secho(f"Error getting deployment status: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
