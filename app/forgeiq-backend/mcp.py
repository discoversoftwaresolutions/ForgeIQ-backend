# forgeiq/mcp_executor.py

from codex_client import generate_code_with_codex
from pathlib import Path

async def execute_dag_task(task_node: dict):
    task_type = task_node.get("type")
    task_id = task_node.get("task_id")

    if task_type == "codegen":
        prompt = task_node.get("prompt", f"Generate boilerplate for task {task_id}")
        code = await generate_code_with_codex(prompt)

        # Save generated code
        output_path = f"src/generated/{task_id}.py"
        Path("src/generated").mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(code)

        return {"status": "codegen_complete", "path": output_path}

    elif task_type == "build":
        # Insert your build logic here
        pass

    elif task_type == "test":
        # Insert your test logic here
        pass

    return {"status": "skipped", "reason": f"Unknown task type: {task_type}"}
