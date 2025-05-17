# ========================================
# 📁 apps/agent-cli/cli_config.py
# ========================================
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class CLIConfig:
    def __init__(self):
        self.api_base_url: Optional[str] = os.getenv("FORGEIQ_API_BASE_URL")
        self.api_key: Optional[str] = os.getenv("FORGEIQ_API_KEY") # Optional for now

        if not self.api_base_url:
            logger.warning(
                "FORGEIQ_API_BASE_URL environment variable not set. "
                "CLI commands requiring API interaction may fail."
            )
            # You could raise an error here or allow it to be set via a command later

# Singleton instance for easy access
cli_config_instance = CLIConfig()
