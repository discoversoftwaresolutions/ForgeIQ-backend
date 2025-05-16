from typing import List, Dict

PROJECT_GRAPH = {
    "debugiq-core": ["lint", "test", "build", "deploy"],
    "forgeiq": ["build", "deploy"],
}

def get_project_dag(project: str) -> List[str]:
    return PROJECT_GRAPH.get(project, [])

def get_affected_tasks(project: str, changed_files: List[str]) -> List[str]:
    dag = get_project_dag(project)
    # Simple heuristic: assume all files change all steps
    return dag
