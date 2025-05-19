# ===============================
# 📁 core/build_graph/index.py
# ===============================
from typing import Dict, List
import hashlib  # ✅ Required for hash_file

# --- Project Graph ---
PROJECT_GRAPH: Dict[str, List[str]] = {
    "debugiq-core": ["lint", "test", "build", "deploy"],
    "forgeiq": ["lint", "build", "deploy"],
}

def get_project_dag(project: str) -> List[str]:
    """Retrieve the DAG tasks for a given project."""
    return PROJECT_GRAPH.get(project, [])

# --- File to Task Mapping ---
FILE_TASK_MAP: Dict[str, List[str]] = {
    "tests/": ["test"],
    "src/": ["lint", "build"],
    "infra/": ["deploy"],
    "README.md": ["lint"],
}

def hash_file(filepath: str) -> str:
    """Generate a SHA-256 hash of a file's content."""
    try:
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return ""

def detect_changed_tasks(project: str, changed_files: List[str]) -> List[str]:
    """Identify tasks impacted by file changes."""
    task_set = set()
    for f in changed_files:
        for path, tasks in FILE_TASK_MAP.items():
            if f.startswith(path) or f == path:
                task_set.update(tasks)

    default_tasks = get_project_dag(project)
    return [task for task in default_tasks if task in task_set or not task_set]
