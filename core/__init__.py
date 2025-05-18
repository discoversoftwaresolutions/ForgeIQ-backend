# =======================
# 📁 core/__init__.py
# =======================
import logging
from typing import List  

# --- Event Bus ---
from .event_bus.redis_bus import EventBus, message_summary

# --- Observability ---
from .observability.tracing import setup_tracing

# --- Shared Memory ---
from .shared_memory.redis_store import SharedMemoryStore

# --- Agent Registry ---
from .agent_registry.registry import AgentRegistry, AgentNotFoundError

# --- Message Router ---
from .message_router.router import MessageRouter, MessageRouteNotFoundError, InvalidMessagePayloadError
# routing_rules.py is used internally by router.py, not typically exported here

# --- Embeddings ---
from .embeddings.embedding_models import EmbeddingModelService
from .embeddings.vector_store_client import VectorStoreClient, CODE_SNIPPET_CLASS_NAME, CODE_SNIPPET_SCHEMA

# --- Build System Configuration ---
from .build_system_config import (
    get_config as get_build_system_config, # Alias for clarity if needed elsewhere
    get_project_build_config,
    get_all_project_configs,
    PROJECT_CONFIGURATIONS, # The direct dictionary of project configs
    get_dag_rules_for_project,
    get_task_weight,
    get_clearance_policy
)

# --- Build Graph (User Provided - assuming functions are in index.py and it's importable) ---
# If build-graph/index.py defines these directly:
try:
    from .build_graph.index import get_project_dag, hash_file, detect_changed_tasks, FILE_TASK_MAP, PROJECT_GRAPH
except ImportError:
    # Fallback if index.py is not the direct source or structure is different
    # This assumes build_graph is a package with __init__.py that exports these.
    # Or that build_graph.py itself contains these.
    # Based on user's code, functions are in core/build_graph/index.py
    # So the above import from .build_graph.index should work if core/build_graph/__init__.py exists.
    # For simplicity, if you ensure core/build_graph/index.py defines these at top level:
    logging.getLogger(__name__).warning(
        "Could not directly import from core.build_graph.index. "
        "Ensure core/build_graph/__init__.py exports these or they are directly in core/build_graph.py"
    )
    # Define placeholders if import fails to prevent further import errors downstream,
    # but this indicates a structural issue to be fixed.
    def get_project_dag(project: str) -> List[str]: return []
    def hash_file(filepath: str) -> str: return ""
    def detect_changed_tasks(project: str, changed_files: List[str]) -> List[str]: return []
    FILE_TASK_MAP: Dict[str, List[str]] = {}
    PROJECT_GRAPH: Dict[str, List[str]] = {}


# --- Task Runner (User Provided - assuming functions are in index.py) ---
try:
    from .task_runner.index import run_task, run_task_sequence, TASK_COMMANDS
except ImportError:
    logging.getLogger(__name__).warning(
        "Could not directly import from core.task_runner.index. "
        "Ensure core/task_runner/__init__.py exports these or they are directly in core/task_runner.py"
    )
    def run_task(task: str, project: str) -> Dict: return {"status": "error", "reason": "task_runner_not_loaded"}
    def run_task_sequence(tasks: List[str], project: str) -> List[Dict]: return [{"status": "error", "reason": "task_runner_not_loaded"}]
    TASK_COMMANDS: Dict[str, List[str]] = {}


# --- Code Utilities (if defined) ---
try:
    from .code_utils.code_parser import (
        scan_code_directory, 
        get_language_from_extension, 
        chunk_code_content,
        CodeChunk
    )
except ImportError:
    logging.getLogger(__name__).warning("core.code_utils.code_parser not found or not fully defined.")
    # Define placeholders
    def scan_code_directory(dir_path: str, ignore_dirs=None, ignore_file_patterns=None, allowed_extensions=None) -> List[str]: return []
    def get_language_from_extension(file_path: str) -> Optional[str]: return None
    def chunk_code_content(content: str, language=None, file_path=None) -> List[Dict[str,Any]]: return []
    CodeChunk = dict #type: ignore


# --- Orchestrator ---
from .orchestrator.main_orchestrator import Orchestrator, OrchestrationError


# Standard library imports needed for the fallback/placeholder definitions above
import logging # Already imported, but good to note for context
from typing import List, Dict, Optional, Any # Already imported

# Define __all__ for explicit public API of the 'core' package
__all__ = [
    "EventBus", "message_summary",
    "setup_tracing",
    "SharedMemoryStore",
    "AgentRegistry", "AgentNotFoundError",
    "MessageRouter", "MessageRouteNotFoundError", "InvalidMessagePayloadError",
    "EmbeddingModelService", "VectorStoreClient", "CODE_SNIPPET_CLASS_NAME", "CODE_SNIPPET_SCHEMA",
    "get_build_system_config", "get_project_build_config", "get_all_project_configs",
    "PROJECT_CONFIGURATIONS", "get_dag_rules_for_project", "get_task_weight", "get_clearance_policy",
    "get_project_dag", "hash_file", "detect_changed_tasks", "FILE_TASK_MAP", "PROJECT_GRAPH", # from build_graph
    "run_task", "run_task_sequence", "TASK_COMMANDS", # from task_runner
    "scan_code_directory", "get_language_from_extension", "chunk_code_content", "CodeChunk", # from code_utils
    "Orchestrator", "OrchestrationError"
]
