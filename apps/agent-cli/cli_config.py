# ========================================
# 📁 apps/agent-cli/app/cli_config.py
# ========================================
import os
import logging
from typing import Optional # For type hints

logger = logging.getLogger(__name__)

class CLIConfig:
    def __init__(self):
        self.api_base_url: Optional[str] = os.getenv("FORGEIQ_API_BASE_URL")
        self.api_key: Optional[str] = os.getenv("FORGEIQ_API_KEY") 

        if not self.api_base_url:
            logger.warning(
                "FORGEIQ_API_BASE_URL environment variable not set. "
                "CLI will default to http://localhost:8000 (assuming Python ForgeIQ Backend default port). "
                "API interactions may fail if this is incorrect."
            )
            self.api_base_url = "http://localhost:8000" 
            # This default assumes your Python/FastAPI ForgeIQ-backend runs on port 8000.
            # Your previous Node.js backend used 3002. Adjust if necessary.
            # The Python FastAPI backend we defined has uvicorn running on port 8000 in its Docker CMD.
            
cli_config_instance = CLIConfig()
