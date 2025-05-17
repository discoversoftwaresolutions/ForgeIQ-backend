# ========================================
# 📁 core/build_system_config.py
# ========================================
from typing import Dict, Any, List

# This is a placeholder for your build system configuration.
# In a real system, this might be loaded from YAML, JSON, a database,
# or have more complex structures.

# Example structure similar to what might have been in buildsystem.config.ts
# but simplified for this Python V0.1.
# This specific content is just an example; you'll replace it with your actual config logic/data.

class ProjectBuildConfig(Dict[str, Any]): # Using Dict for flexibility, Pydantic model for stricter schema
    pass

DEFAULT_TASK_WEIGHTS: Dict[str, Any] = {
    "lint": {"cpu": 1, "memory": 256},
    "test": {"cpu": 2, "memory": 512},
    "build": {"cpu": 2, "memory": 1024},
    "deploy": {"cpu": 1, "memory": 512, "network": True},
}

DEFAULT_CLEARANCE_POLICIES: Dict[str, Any] = {
    "staging_deploy": {"min_approvals": 0, "required_checks": ["lint", "test", "build"]},
    "production_deploy": {"min_approvals": 1, "required_checks": ["lint", "test", "build", "security_scan_passed"]},
}

# Example project-specific configurations
# You would populate this with actual configuration data for each project.
PROJECT_CONFIGURATIONS: Dict[str, ProjectBuildConfig] = {
    "project_alpha": {
        "description": "Project Alpha configuration for ForgeIQ.",
        "supported_tasks": ["lint", "test", "build", "deploy_staging"],
        "default_environment": "staging",
        "task_weights": {**DEFAULT_TASK_WEIGHTS, "custom_task_alpha": {"cpu": 3, "memory": 2048}},
        "clearance_policies": DEFAULT_CLEARANCE_POLICIES,
        "notifications": {"on_failure": "devops-alerts@example.com"}
    },
    "project_beta": {
        "description": "Project Beta configuration.",
        "supported_tasks": ["lint", "test", "build_frontend", "build_backend", "deploy_all"],
        "default_environment": "development",
        "task_weights": DEFAULT_TASK_WEIGHTS,
        "clearance_policies": DEFAULT_CLEARANCE_POLICIES,
        "source_control": {"type": "git", "default_branch": "main"}
    },
    # Add more projects as needed
}

def get_project_build_config(project_id: str) -> Optional[ProjectBuildConfig]:
    return PROJECT_CONFIGURATIONS.get(project_id)

def get_all_project_configs() -> Dict[str, ProjectBuildConfig]:
    return PROJECT_CONFIGURATIONS

# You could add more functions here to access specific parts of the config.
