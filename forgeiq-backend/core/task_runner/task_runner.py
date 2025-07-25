# File: forgeiq-backend/core/task_runner/index.py

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# Define your TASK_COMMANDS mapping here
# This is a critical dictionary that maps task names to their execution details.
TASK_COMMANDS: Dict[str, Dict[str, Any]] = {
    "build_code": {
        "description": "Compiles and builds the provided source code.",
        "command_template": ["/usr/bin/builder-cli", "--source", "{source_path}", "--output", "{output_path}"],
        "default_timeout_seconds": 300 # 5 minutes
    },
    "run_unit_tests": {
        "description": "Executes unit tests for the project.",
        "command_template": ["pytest", "{test_suite_path}"],
        "default_timeout_seconds": 180 # 3 minutes
    },
    "deploy_artifact": {
        "description": "Deploys a build artifact to a staging environment.",
        "command_template": ["deploy-tool", "push", "--artifact", "{artifact_url}", "--env", "{environment}"],
        "default_timeout_seconds": 600 # 10 minutes
    },
    "analyze_security": {
        "description": "Performs security analysis on generated code.",
        "command_template": ["snyk", "test", "{code_path}"],
        "default_timeout_seconds": 300
    },
    "llm_code_review": { # Example of an LLM-related command
        "description": "Performs an automated code review using an LLM.",
        "command_template": ["python", "-m", "your_llm_agent_cli", "review", "--code", "{code_snippet_path}"]
    },
    "dag_optimization": { # Example for algorithms
        "description": "Optimizes a DAG using proprietary algorithms.",
        "command_template": ["python", "-m", "your_algo_cli", "optimize", "--dag", "{dag_json_path}"]
    }
}

logger.info(f"Loaded {len(TASK_COMMANDS)} task commands for ForgeIQ Backend.")
