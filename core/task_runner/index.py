from typing import List, Dict
import subprocess
import logging
from typing import Any, Dict, List  # ✅ Ensures `Any` is recognized

# ✅ Logger Setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- Task Definitions ---
TASK_COMMANDS: Dict[str, List[str]] = {
    "lint": ["echo", "Running linter..."],
    "test": ["pytest", "--maxfail=1", "--disable-warnings"],  # ✅ Ensure pytest is in your requirements.txt
    "build": ["echo", "Building the project..."],
    "deploy": ["echo", "Deploying..."]
}

def run_task(task: str, project: str) -> Dict[str, Any]:
    """Executes a given task for a project."""
    cmd = TASK_COMMANDS.get(task)
    if not cmd:
        return {
            "task": task,
            "project": project,
            "status": "skipped",
            "reason": "Unknown task"
        }

    logger.info(f"[TaskRunner] Executing '{task}' for project '{project}'")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return {
            "task": task,
            "project": project,
            "status": "success",
            "output": result.stdout.strip()
        }
    except subprocess.CalledProcessError as e:
        logger.error(f"[TaskRunner] Error executing '{task}' for project '{project}': {e.stderr.strip()}")
        return {
            "task": task,
            "project": project,
            "status": "error",
            "output": e.stderr.strip(),
            "exit_code": e.returncode
        }
    except FileNotFoundError:
        logger.error(f"[TaskRunner] Command not found for task '{task}': {cmd[0]}")
        return {
            "task": task,
            "project": project,
            "status": "error",
            "reason": f"Command not found: {cmd[0]}"
        }

def run_task_sequence(tasks: List[str], project: str) -> List[Dict[str, Any]]:
    """Executes a sequence of tasks for a project, stopping on failure if necessary."""
    results = []
    for task in tasks:
        result = run_task(task, project)
        results.append(result)
        
        if result.get("status") == "error":  # ✅ Stop sequence on first error
            logger.warning(f"[TaskRunner] Task '{task}' failed for project '{project}'. Stopping sequence.")
            break
    
    return results
