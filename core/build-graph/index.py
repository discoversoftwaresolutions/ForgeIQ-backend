# ===============================
# 📁 core/build-graph/index.py
# ===============================

from typing import List, Dict
import hashlib
import os

# Simulated file mapping to tasks (normally generated via heuristics or manifest)
FILE_TASK_MAP = {
    "tests/": ["test"],
    "src/": ["lint", "build"],
    "infra/": ["deploy"],
    "README.md": ["lint"]
}

# Full project DAGs
PROJECT_GRAPH: Dict[str, List[str]] = {
    "debugiq-core": ["lint", "test", "build", "deploy"],
    "forgeiq": ["lint", "build", "deploy"],
}

def get_project_dag(project: str) -> List[str]:
    return PROJECT_GRAPH.get(project, [])

def hash_file(filepath: str) -> str:
    try:
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return ""

def detect_changed_tasks(project: str, changed_files: List[str]) -> List[str]:
    task_set = set()
    for f in changed_files:
        for path, tasks in FILE_TASK_MAP.items():
            if f.startswith(path) or f == path:
                task_set.update(tasks)
    default_tasks = get_project_dag(project)
    return [task for task in default_tasks if task in task_set or not task_set]
