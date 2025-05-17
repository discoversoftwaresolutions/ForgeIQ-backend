# ====================================
# 📁 apps/agent-cli/app/main.py
# ====================================
import typer
import logging
import os
import asyncio 
import atexit 
import sys # For sys.stderr in logging config if preferred

# Relative imports for command modules and SDK client factory
from .commands import orchestrate_cmds, status_cmds 
from .sdk_client_factory import close_sdk_client 

# --- Configure Root Logger for CLI ---
# This ensures logs from SDK and other modules are visible based on CLI verbosity.
CLI_LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
# Convert string level to logging level integer
numeric_log_level = getattr(logging, CLI_LOG_LEVEL_STR, logging.INFO)

# Configure basic logging to output to stderr for CLI, so stdout can be used for command output.
# Using a more robust format.
logging.basicConfig(
    level=numeric_log_level,
    format='%(asctime)s [%(levelname)-8s] %(name)-25s: %(message)s', # Adjusted format
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stderr # Explicitly send logs to stderr
)
# Example: Quieten overly verbose libraries if necessary
# logging.getLogger("httpx").setLevel(logging.WARNING)
# logging.getLogger("httpcore").setLevel(logging.WARNING)
# --- End Logger Config ---

# Create the main Typer application instance
app = typer.Typer(
    name="forgecli",
    help="ForgeIQ Agentic Build System CLI - Interact with and manage your ForgeIQ services and agents.",
    no_args_is_help=True, # Show help if no command is given
    add_completion=False # Disable shell completion for simplicity now
)

# Add sub-command groups (from other files) to the main app
app.add_typer(orchestrate_cmds.orchestrate_app, name="orchestrate") # Use the app instance from the module
app.add_typer(status_cmds.status_app, name="status")          # Use the app instance from the module

@app.callback(invoke_without_command=True) # invoke_without_command ensures this runs even if a subcommand is called
def main_cli_setup(
    ctx: typer.Context, # Context object, useful if no_args_is_help=False and we want to print help
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose DEBUG logging for all loggers.")
):
    """
    ForgeIQ CLI Main Entry Point.
    Global options like --verbose are handled here.
    """
    if verbose:
        # Set root logger to DEBUG, affecting all loggers unless they have specific higher levels
        logging.getLogger().setLevel(logging.DEBUG) 
        for handler in logging.getLogger().handlers: # Ensure all handlers also get the level
            handler.setLevel(logging.DEBUG)

        # Get a logger for this module specifically if needed for a debug message
        module_logger = logging.getLogger(__name__) 
        module_logger.debug("Verbose (DEBUG) logging enabled for ForgeIQ CLI and its modules.")

    # If no command was given and no_args_is_help is False for app, you might print help:
    # if ctx.invoked_subcommand is None and not app.no_args_is_help:
    #     typer.echo(ctx.get_help())


# --- Graceful SDK client shutdown on exit ---
_sdk_client_has_been_closed = False # Flag to prevent multiple close attempts

async def _perform_sdk_shutdown():
    """Async helper to actually close the SDK client."""
    global _sdk_client_has_been_closed
    if not _sdk_client_has_been_closed:
        logger.debug("Attempting to close SDK client via atexit handler...")
        await close_sdk_client() # close_sdk_client is async
        _sdk_client_has_been_closed = True
        logger.debug("SDK client shutdown complete via atexit.")

def synchronous_shutdown_hook():
    """Synchronous wrapper for atexit to call the async shutdown."""
    logger.debug("atexit: synchronous_shutdown_hook called.")
    try:
        # Check if an event loop is already running.
        # This can be tricky with Typer's internal asyncio.run for commands.
        # A simple asyncio.run() here is often the most straightforward for atexit.
        asyncio.run(_perform_sdk_shutdown())
    except RuntimeError as e:
        # This might happen if asyncio.run is called when a loop is already running
        # or if called from a non-main thread where a new loop can't be set.
        # This part of atexit with asyncio can be finicky.
        logger.error(f"atexit: RuntimeError during asyncio.run for shutdown: {e}. SDK client might not be closed cleanly.")
    except Exception as e:
        logger.error(f"atexit: Unexpected error during SDK client shutdown: {e}", exc_info=True)

atexit.register(synchronous_shutdown_hook)
# --- End Graceful Shutdown ---


# This allows running the CLI by executing this main.py file directly
if __name__ == "__main__":
    # To run from monorepo root (ensure PYTHONPATH includes sdk, core, interfaces etc.):
    # Example:
    # export FORGEIQ_API_BASE_URL="http://localhost:8000" # If ForgeIQ-backend (Python/FastAPI) runs on 8000
    # export LOG_LEVEL="DEBUG"
    # export PYTHONPATH=$PYTHONPATH:$(pwd)
    # python -m apps.agent_cli.app.main --help
    # python -m apps.agent_cli.app.main orchestrate start-build --project-id myproj --commit-sha abc1234
    # python -m apps.agent_cli.app.main status dag --project-id myproj --dag-id testdag
    app()
