# ====================================
# 📁 app/agent-cli/main.py
# ====================================
import typer
import logging
import os
import asyncio 
import atexit 
import sys # For sys.stderr

from .commands import orchestrate_cmds, status_cmds, buildsys_cmds 
from .sdk_client_factory import close_sdk_client_on_exit 

# --- Configure Root Logger for CLI ---
CLI_LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
numeric_log_level = getattr(logging, CLI_LOG_LEVEL_STR, logging.INFO)
logging.basicConfig(
    level=numeric_log_level,
    format='%(asctime)s [%(levelname)-8s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stderr 
)
# --- End Logger Config ---

app = typer.Typer(
    name="forgecli",
    help="ForgeIQ Agentic Build System CLI.",
    no_args_is_help=True,
    add_completion=False 
)

app.add_typer(orchestrate_cmds.orchestrate_app, name="orchestrate")
app.add_typer(status_cmds.status_app, name="status")
app.add_typer(buildsys_cmds.buildsys_app, name="buildsystem")

@app.callback(invoke_without_command=True) 
def main_cli_setup(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging.")
):
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG) 
        for handler in logging.getLogger().handlers: handler.setLevel(logging.DEBUG)
        logging.getLogger(__name__).debug("Verbose (DEBUG) logging enabled.")

# --- Graceful SDK client shutdown ---
_sdk_client_has_been_closed = False # Module-level flag
async def _perform_sdk_shutdown():
    global _sdk_client_has_been_closed
    if not _sdk_client_has_been_closed:
        # Assuming close_sdk_client_on_exit is an async function
        await close_sdk_client_on_exit() 
        _sdk_client_has_been_closed = True
def synchronous_shutdown_hook():
    logger.debug("atexit: synchronous_shutdown_hook called for SDK client.")
    try: asyncio.run(_perform_sdk_shutdown())
    except RuntimeError as e: logger.error(f"atexit: RuntimeError during SDK shutdown: {e}")
    except Exception as e: logger.error(f"atexit: Unexpected error during SDK shutdown: {e}", exc_info=True)
atexit.register(synchronous_shutdown_hook)
# --- End Graceful Shutdown ---

if __name__ == "__main__":
    app()
