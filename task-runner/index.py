from typing import List, Dict

def run_task(task: str, project: str) -> Dict:
    print(f"[TaskRunner] Executing {task} for project {project}")
    return {
        "task": task,
        "project": project,
        "status": "success",
        "output": f"Completed {task} for {project}"
    }

def run_task_sequence(tasks: List[str], project: str) -> List[Dict]:
    results = []
    for task in tasks:
        result = run_task(task, project)
        results.append(result)
    return results
