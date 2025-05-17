# =================================
# 📁 interfaces/types/graph.py
# =================================
from typing import TypedDict, List, Dict, Any, Optional

class DagNode(TypedDict):
    id: str            # Unique ID for the node/task within the DAG
    task_type: str     # e.g., 'lint', 'test', 'build', 'deploy', 'custom_script'
    command: Optional[List[str]] # Actual command for task-runner if simple
    agent_handler: Optional[str] # Which agent should handle this node
    params: Optional[Dict[str, Any]]
    dependencies: List[str] # List of other node IDs this node depends on

class DagDefinition(TypedDict):
    dag_id: str
    project_id: Optional[str]
    nodes: List[DagNode]
    description: Optional[str]
    # Could add metadata like trigger conditions, overall timeout, etc.
