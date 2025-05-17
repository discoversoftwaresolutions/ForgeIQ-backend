# ==============================================
# 📁 apps/agent-cli/app/sdk_client_factory.py
# ==============================================
import logging
from typing import Optional

# Assuming your SDK is in a top-level 'sdk' directory and importable
# This requires 'sdk' to be in PYTHONPATH when running the CLI
from sdk import ForgeIQClient, HookManager # Import from your Python SDK
from .cli_config import cli_config_instance

logger = logging.getLogger(__name__)
_sdk_client_instance: Optional[ForgeIQClient] = None

def get_sdk_client() -> ForgeIQClient:
    """
    Provides a singleton instance of the ForgeIQClient, configured
    from environment variables or a config file via CLIConfig.
    """
    global _sdk_client_instance
    if _sdk_client_instance is None:
        if not cli_config_instance.api_base_url:
            # Logged in CLIConfig, but good to emphasize for client creation
            logger.error("Cannot create SDK client: FORGEIQ_API_BASE_URL is not configured.")
            # Raise an exception or handle gracefully depending on CLI command context
            raise RuntimeError("ForgeIQ API base URL not configured. Set FORGEIQ_API_BASE_URL.")

        # You can initialize HookManager here if CLI needs to register hooks
        # For V0.1, SDK client can create its own default HookManager.
        # hook_manager = HookManager() 
        try:
            _sdk_client_instance = ForgeIQClient(
                base_url=cli_config_instance.api_base_url,
                api_key=cli_config_instance.api_key
                # hook_manager=hook_manager # if you want to pass a shared one
            )
            logger.info(f"ForgeIQSDKClient initialized for CLI, targeting: {cli_config_instance.api_base_url}")
        except ValueError as ve: # Catch ValueError from ForgeIQClient if base_url is still None
            logger.error(f"ValueError during SDK client initialization: {ve}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error initializing SDK client: {e}", exc_info=True)
            raise RuntimeError(f"Could not initialize ForgeIQ SDK client: {e}") from e

    return _sdk_client_instance

async def close_sdk_client():
    """Closes the SDK client if it was initialized."""
    global _sdk_client_instance
    if _sdk_client_instance:
        try:
            await _sdk_client_instance.close()
            logger.info("ForgeIQSDKClient connection closed by CLI.")
        except Exception as e:
            logger.error(f"Error closing SDK client: {e}", exc_info=True)
        finally:
            _sdk_client_instance = None # Reset for potential re-init
