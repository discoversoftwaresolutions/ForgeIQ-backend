# ====================================================
# 📁 apps/agent-cli/app/commands/orchestrate_cmds.py
# ====================================================
import typer
import asyncio
import logging
from typing_extensions import Annotated # For Typer <0.9 compatibility, or use just typing for Python 3.9+
from typing import Optional, List

from ..sdk_client_factory import get_sdk_client # Relative import

logger = logging.getLogger(__name__)
app = typer.Typer(name="orchestrate", help="Commands to trigger and manage orchestration flows.")

@app.command("start-build")
def start_build_flow(
    project_id: Annotated[str, typer.Option(help="The ID of the project to build.")],
    commit_sha: Annotated[str, typer.Option(help="The commit SHA to build.")],
    changed_files: Annotated[Optional[List[str]], typer.Option("--changed-file", "-f", help="Path of a changed file (can be specified multiple times).")] = None,
    prompt: Annotated[Optional[str], typer.Option(help="User prompt for dynamic pipeline generation via BuildSurfAgent.")] = None,
    request_id: Annotated[Optional[str], typer.Option(help="Custom request ID for tracking.")] = None
):
    """
    Starts a full build flow for a project and commit,
    optionally with a prompt for dynamic pipeline generation.
    """
    typer.echo(f"Attempting to start build flow for project: {project_id}, commit: {commit_sha}")
    if prompt:
        typer.echo(f"Using user prompt: {prompt}")
    if changed_files:
        typer.echo(f"Changed files reported: {', '.join(changed_files)}")

    sdk_client = get_sdk_client() # Gets initialized ForgeIQClient

    # The Orchestrator's run_full_build_flow expects List[str] for changed_files_list
    # Our Python SDK's ForgeIQClient.submit_pipeline_prompt takes a different structure
    # This CLI needs to call an API endpoint on ForgeIQ-backend that triggers the Orchestrator.
    # Let's assume ForgeIQ-backend has an endpoint /api/forgeiq/orchestrate/start-full-build-flow
    # For now, we'll use the submit_pipeline_prompt as a proxy if no specific orchestrator endpoint exists.
    # This highlights that the SDK and backend API might need a dedicated "start full flow" endpoint.

    # For this V0.1, let's simplify: if a prompt is given, we use submit_pipeline_prompt.
    # If not, we'd need another mechanism or assume a default DAG for the project.
    # This CLI command is more about *triggering* the concept of the orchestrator's flow.

    async def _run():
        try:
            # This is a conceptual call. The SDK's submit_pipeline_prompt is for BuildSurfAgent.
            # An orchestrator might be triggered by a more generic "start flow" event or API.
            # For now, we'll use submit_pipeline_prompt if a prompt exists.
            if prompt:
                response = await sdk_client.submit_pipeline_prompt(
                    project_id=project_id,
                    user_prompt=prompt,
                    # additional_context could include commit_sha and changed_files
                    additional_context={"commit_sha": commit_sha, "changed_files_hint": changed_files or []},
                    request_id=request_id
                )
                typer.echo(f"Pipeline generation request submitted successfully:")
                typer.echo(json.dumps(response, indent=2)) # Assuming JSON response
            else:
                # If no prompt, how do we trigger a default build flow for a commit?
                # This would require an API on ForgeIQ-backend to initiate an orchestration
                # based on a commit, which then uses DependencyAgent and PlanAgent.
                # For V0.1 CLI, we'll just log this path.
                typer.echo(f"No prompt provided. Triggering a default build flow for project '{project_id}', commit '{commit_sha}' needs a dedicated API endpoint.")
                typer.echo("Conceptual: Calling backend to start flow based on commit and affected tasks...")
                # Example (if such an endpoint existed):
                # response = await sdk_client.http_client.post(f"/api/forgeiq/orchestrate/commit-build", 
                #                                           json={"project_id": project_id, "commit_sha": commit_sha, "changed_files": changed_files or []})
                # typer.echo(f"Response: {response.json()}")
                typer.echo("Note: This specific CLI command path (no prompt) is conceptual for V0.1 until backend API supports it.")

        except Exception as e:
            logger.error(f"CLI error during start-build: {e}", exc_info=True)
            typer.secho(f"Error starting build flow: {e}", fg=typer.colors.RED, err=True)
        # finally: # Closing client managed by main.py atexit or context manager
            # await sdk_client.close() # Not here, client is singleton for CLI session

    asyncio.run(_run())
