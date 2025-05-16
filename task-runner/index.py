# ===============================
# 📁 core/task-runner/index.py
# ===============================

from typing import List, Dict
import subprocess
import logging

logging.basicConfig(level=logging.INFO)

TASK_COMMANDS = {
    "lint": ["echo", "Running linter..."],
    "test": ["pytest", "--maxfail=1", "--disable-warnings"], # Ensure pytest is in your requirements.txt
    "build": ["echo", "Building the project..."],
    "deploy": ["echo", "Deploying..."]
}

def run_task(task: str, project: str) -> Dict:
    cmd = TASK_COMMANDS.get(task)
    if not cmd:
        return {
            "task": task,
            "project": project,
            "status": "skipped",
            "reason": "Unknown task"
        }

    logging.info(f"[TaskRunner] Executing '{task}' for project '{project}'")
    try:
        # For commands like 'pytest', ensure the command can be found in the PATH
        # or provide full paths if necessary, especially when run from different contexts.
        # Also, consider the working directory if these commands depend on it.
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return {
            "task": task,
            "project": project,
            "status": "success",
            "output": result.stdout.strip()
        }
    except subprocess.CalledProcessError as e:
        return {
            "task": task,
            "project": project,
            "status": "error",
            "output": e.stderr.strip(),
            "exit_code": e.returncode
        }
    except FileNotFoundError: # Handle case where a command itself (e.g. 'pytest') isn't found
        logging.error(f"[TaskRunner] Command not found for task '{task}': {cmd[0]}")
        return {
            "task": task,
            "project": project,
            "status": "error",
            "reason": f"Command not found: {cmd[0]}"
        }


def run_task_sequence(tasks: List[str], project: str) -> List[Dict]:
    results = []
    for task in tasks:
        result = run_task(task, project)
        results.append(result)
        if result.get("status") == "error": # Optional: Stop sequence on first error
            logging.warning(f"[TaskRunner] Task '{task}' failed for project '{project}'. Stopping sequence.")
            break 
    return results
