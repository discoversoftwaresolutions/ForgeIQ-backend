# ==============================================
# 📁 apps/agent-cli/app/sdk_client_factory.py
# ==============================================
import logging
import os # Though not directly used here, often useful in config/factory patterns
from typing import Optional

try:
    from sdk.client import ForgeIQClient 
    # from sdk.hooks import HookManager # HookManager not used in this factory V0.1
except ImportError as e:
    logging.getLogger(__name__).critical(
        f"FATAL: Failed to import ForgeIQClient from SDK: {e}. "
        "Ensure 'sdk' package is in PYTHONPATH and correctly structured with __init__.py files."
    )
    raise SystemExit(f"SDK Import Error: {e}") from e

from .cli_config import cli_config_instance

logger = logging.getLogger(__name__)
_sdk_client_instance: Optional[ForgeIQClient] = None

def get_sdk_client() -> ForgeIQClient:
    global _sdk_client_instance
    if _sdk_client_instance is None:
        if not cli_config_instance.api_base_url:
            logger.error("Cannot create SDK client: FORGEIQ_API_BASE_URL is not configured.")
            raise RuntimeError("ForgeIQ API base URL not configured. Set FORGEIQ_API_BASE_URL.")
        
        try:
            _sdk_client_instance = ForgeIQClient(
                base_url=cli_config_instance.api_base_url,
                api_key=cli_config_instance.api_key
            )
            logger.debug(f"ForgeIQSDKClient initialized for CLI, targeting: {cli_config_instance.api_base_url}")
        except ValueError as ve: 
            logger.error(f"ValueError during SDK client initialization: {ve}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error initializing SDK client: {e}", exc_info=True)
            raise RuntimeError(f"Could not initialize ForgeIQ SDK client: {e}") from e
    return _sdk_client_instance

async def close_sdk_client_on_exit():
    global _sdk_client_instance
    if _sdk_client_instance:
        try:
            await _sdk_client_instance.close() 
            logger.debug("ForgeIQSDKClient connection closed by CLI atexit handler.")
        except Exception as e:
            logger.error(f"Error closing SDK client during CLI exit: {e}", exc_info=True)
        finally:
            _sdk_client_instance = None
