# ==============================================
# 📁 core/orchestrator/main_orchestrator.py
# ==============================================
import os
import json
import asyncio
import logging
import uuid
import datetime
import time  # <<< Added for time.monotonic()
import hashlib # <<< Added for example usage block
from typing import Dict, Any, Optional, List

# --- Observability Setup ---
SERVICE_NAME_ORCHESTRATOR = "Orchestrator"
LOG_LEVEL_ORCHESTRATOR = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL_ORCHESTRATOR,
