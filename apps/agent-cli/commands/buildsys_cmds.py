# ===================================================
# 📁 apps/agent-cli/commands/buildsys_cmds.py
# ===================================================
import typer
import asyncio # Typer uses this for async commands
import json
import logging
from typing import Optional, List # For type hints
try: from typing import Annotated 
except ImportError: from typing_extensions import Annotated

from ..sdk_client_factory import get_sdk_client
# Access BuildSystemClient via client.build_system property as defined in response #71 for sdk/client.py

logger = logging.getLogger(__name__)
buildsys_app = typer.Typer(name="buildsystem", help="Commands to query build system configurations and tasks.")

@buildsys_app.command("get-config")
async def get_project_config_cmd( # Command function is async
    project_id: Annotated[str, typer.Option(help="Project ID to get build configuration for.")]
):
    """Retrieves the build system configuration for a specific project."""
    typer.echo(f"Fetching build configuration for project: {project_id}...")
    sdk_client = get_sdk_client()
    try:
        # client.build_system was added as a property to ForgeIQClient, returning BuildSystemClient instance
        if not hasattr(sdk_client, 'build_system') or \
           not hasattr(sdk_client.build_system, 'get_project_configuration'): # Check method existence
            typer.secho("SDK structure error: build_system client or method not found.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        config_data = await sdk_client.build_system.get_project_configuration(project_id=project_id)
        typer.echo(f"Configuration for Project '{project_id}':")
        typer.secho(json.dumps(config_data, indent=2), fg=typer.colors.GREEN)
    except Exception as e:
        logger.error(f"CLI error getting project config for '{project_id}': {e}", exc_info=True)
        typer.secho(f"Error getting project configuration: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

@buildsys_app.command("list-tasks")
async def list_tasks_cmd( # Command function is async
    project_id: Annotated[Optional[str], typer.Option(help="Optional Project ID to filter tasks.")] = None
):
    """Lists available tasks, optionally filtered by project."""
    typer.echo(f"Fetching available tasks (Project: {project_id or 'all'})...")
    sdk_client = get_sdk_client()
    try:
        if not hasattr(sdk_client, 'build_system') or \
           not hasattr(sdk_client.build_system, 'list_available_tasks'):
            typer.secho("SDK structure error: build_system client or method for listing tasks not found.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
            
        tasks_data = await sdk_client.build_system.list_available_tasks(project_id=project_id)
        typer.echo("Available Tasks:")
        # tasks_data from SDK's BuildSystemClient is expected to be List[Dict] (TaskDefinitionModel compatible)
        typer.secho(json.dumps(tasks_data, indent=2), fg=typer.colors.GREEN) 
    except Exception as e:
        logger.error(f"CLI error listing tasks: {e}", exc_info=True)
        typer.secho(f"Error listing tasks: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
