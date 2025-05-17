# ================================================
# 📁 apps/agent-cli/app/commands/status_cmds.py
# ================================================
import typer
import asyncio
import json # For pretty printing dicts
import logging
from typing_extensions import Annotated
from typing import Optional

from ..sdk_client_factory import get_sdk_client

logger = logging.getLogger(__name__)
app = typer.Typer(name="status", help="Commands to query status of various system components.")

@app.command("dag")
def get_dag_status_cmd(
    project_id: Annotated[str, typer.Option(help="Project ID for the DAG.")],
    dag_id: Annotated[str, typer.Option(help="The ID of the DAG to query.")]
):
    """Gets the execution status of a specific DAG."""
    typer.echo(f"Querying status for DAG '{dag_id}' in project '{project_id}'...")
    sdk_client = get_sdk_client()
    async def _run():
        try:
            status_data = await sdk_client.get_dag_execution_status(project_id=project_id, dag_id=dag_id)
            typer.echo("DAG Execution Status:")
            typer.echo(json.dumps(status_data, indent=2)) # Assumes status_data is a dict (from SDK model)
        except Exception as e:
            logger.error(f"CLI error getting DAG status: {e}", exc_info=True)
            typer.secho(f"Error getting DAG status: {e}", fg=typer.colors.RED, err=True)
    asyncio.run(_run())

@app.command("deployment")
def get_deployment_status_cmd(
    project_id: Annotated[str, typer.Option(help="Project ID.")],
    service_name: Annotated[str, typer.Option(help="Service name on Railway.")],
    request_id: Annotated[str, typer.Option(help="The deployment request ID to query.")]
):
    """Gets the status of a specific deployment request."""
    typer.echo(f"Querying deployment status for request ID '{request_id}' (service: {service_name}, project: {project_id})...")
    sdk_client = get_sdk_client()
    async def _run():
        try:
            status_data = await sdk_client.get_deployment_status(
                project_id=project_id, 
                service_name=service_name, 
                deployment_request_id=request_id
            )
            typer.echo("Deployment Status:")
            typer.echo(json.dumps(status_data, indent=2))
        except Exception as e:
            logger.error(f"CLI error getting deployment status: {e}", exc_info=True)
            typer.secho(f"Error getting deployment status: {e}", fg=typer.colors.RED, err=True)
    asyncio.run(_run())
