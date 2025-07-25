# File: forgeiq-backend/core/task_runner/index.py

from typing import Dict, Any, List

# Define your TASK_COMMANDS mapping here
# Example structure, replace with your actual task definitions
TASK_COMMANDS: Dict[str, Dict[str, Any]] = {
    "build_code": {
        "description": "Compiles and builds the provided source code.",
        "command_template": ["/usr/bin/builder-cli", "--source", "{source_path}", "--output", "{output_path}"]
    },
    "run_unit_tests": {
        "description": "Executes unit tests for the project.",
        "command_template": ["pytest", "{test_suite_path}"]
    },
    "deploy_artifact": {
        "description": "Deploys a build artifact to a staging environment.",
        "command_template": ["deploy-tool", "push", "--artifact", "{artifact_url}", "--env", "{environment}"]
    },
    "analyze_security": {
        "description": "Performs security analysis on generated code.",
        "command_template": ["snyk", "test", "{code_path}"]
    }
}
