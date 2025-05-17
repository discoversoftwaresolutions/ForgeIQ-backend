# ===================================================
# 📁 core/build_system_config.py (Corrected for Imports)
# ===================================================
import logging
import asyncio # For asyncio.Lock and async functions
from typing import Dict, Any, List, TypedDict, Optional, Literal # For type hints

logger = logging.getLogger(__name__)

# --- TypedDicts for Configuration Clarity (ensure these match your needs) ---
class DagRuleConfig(TypedDict):
    allow_cycles: bool
    max_depth: Optional[int]
    default_timeout_seconds: Optional[int]

class TaskWeightConfig(TypedDict):
    cpu_units: float 
    memory_mb: int   
    priority: int    

class ClearancePolicyRule(TypedDict):
    required_checks: List[str] 
    min_approvals: Optional[int]
    auto_approve_on_success: Optional[bool]

class ClearancePoliciesConfig(TypedDict):
    default: Optional[ClearancePolicyRule]
    production_deployment: Optional[ClearancePolicyRule]
    # Add other specific policies as needed, e.g., staging_release: Optional[ClearancePolicyRule]

class ProjectSpecificTaskConfig(TypedDict):
    task_weights: Optional[Dict[str, TaskWeightConfig]] # Override global task weights
    task_parameters: Optional[Dict[str, Dict[str, Any]]] # Predefined params for common tasks

class ProjectConfig(TypedDict):
    description: Optional[str]
    dag_rules: Optional[DagRuleConfig] # Override global DAG rules
    task_config: Optional[ProjectSpecificTaskConfig]
    clearance_policies: Optional[ClearancePoliciesConfig] # Override global clearance policies
    # Add other project-specific build system configurations

class BuildSystemConfig(TypedDict):
    global_dag_rules: DagRuleConfig
    global_task_weights: Dict[str, TaskWeightConfig] # Task type name -> TaskWeightConfig
    global_clearance_policies: ClearancePoliciesConfig
    projects: Dict[str, ProjectConfig] # Project ID -> ProjectConfig

# --- Default Configuration Data (V0.1 - Hardcoded) ---
# This is where your system's default operational parameters are defined.
# In a more advanced setup, this could be loaded from a YAML or JSON file.
_DEFAULT_CONFIG_DATA: BuildSystemConfig = {
    "global_dag_rules": { # DagRuleConfig
        "allow_cycles": False,
        "max_depth": 50,
        "default_timeout_seconds": 3 * 60 * 60 # 3 hours
    },
    "global_task_weights": { # TaskWeightConfig for various task types
        "lint": {"cpu_units": 0.5, "memory_mb": 512, "priority": 5},
        "test_unit": {"cpu_units": 1.0, "memory_mb": 1024, "priority": 3},
        "test_integration": {"cpu_units": 2.0, "memory_mb": 2048, "priority": 3},
        "security_sast": {"cpu_units": 1.5, "memory_mb": 2048, "priority": 4},
        "security_sca": {"cpu_units": 1.0, "memory_mb": 1024, "priority": 4},
        "build_python_wheel": {"cpu_units": 1.0, "memory_mb": 1024, "priority": 2},
        "build_docker_image": {"cpu_units": 2.0, "memory_mb": 4096, "priority": 2},
        "deploy_staging": {"cpu_units": 0.5, "memory_mb": 512, "priority": 1},
        "deploy_production": {"cpu_units": 1.0, "memory_mb": 1024, "priority": 0},
    },
    "global_clearance_policies": { # ClearancePoliciesConfig
        "default": {"required_checks": [], "min_approvals": 0, "auto_approve_on_success": True}, # ClearancePolicyRule
        "production_deployment": { # ClearancePolicyRule
            "required_checks": ["all_tests_passed", "security_scan_clean", "manual_approval_product_owner"],
            "min_approvals": 1,
            "auto_approve_on_success": False
        }
    },
    "projects": { # Dict[str, ProjectConfig]
        "forgeiq_backend_py": { 
            "description": "ForgeIQ Backend Service (Python/FastAPI)",
            "dag_rules": {"allow_cycles": False, "max_depth": 30, "default_timeout_seconds": 3600}, 
            "task_config": {
                "task_parameters": {"deploy_staging": {"target_service_name": "forgeiq-backend-staging"}}
            },
            "clearance_policies": {
                 "production_deployment": {
                    "required_checks": ["all_tests_passed", "security_scan_clean", "perf_tests_passed", "manual_approval_lead_dev"],
                    "min_approvals": 2
                }
            }
        },
        "example_agent_project": {
            "description": "An example agent project configuration",
            # Can have its own dag_rules, task_config, clearance_policies here to override globals
        }
        # Define other projects here
    }
}

# --- Configuration Access Functions and Exportable Variables ---

# This makes the 'projects' part of the config directly importable as PROJECT_CONFIGURATIONS
PROJECT_CONFIGURATIONS: Dict[str, ProjectConfig] = _DEFAULT_CONFIG_DATA["projects"]

_loaded_config_singleton: Optional[BuildSystemConfig] = None
_config_init_lock = asyncio.Lock() # For async-safe lazy initialization

def _load_config_from_source_sync() -> BuildSystemConfig:
    """
    Internal synchronous function to load configuration.
    For V0.1, it returns the hardcoded default.
    Future: Could load from os.getenv("BUILDSYSTEM_CONFIG_FILE_PATH") using 'json' or 'yaml' modules.
    """
    logger.debug("Using hardcoded default build system configuration for V0.1.")
    return _DEFAULT_CONFIG_DATA

async def get_config() -> BuildSystemConfig:
    """Async function to get the global configuration, ensuring it's loaded once."""
    global _loaded_config_singleton
    if _loaded_config_singleton is None:
        async with _config_init_lock:
            if _loaded_config_singleton is None: # Double-check after acquiring lock
                # If _load_config_from_source_sync becomes async in future, await it directly.
                # For now, if it's sync, wrap with to_thread for async context.
                _loaded_config_singleton = await asyncio.to_thread(_load_config_from_source_sync)
    return _loaded_config_singleton

async def get_project_build_config(project_id: str) -> Optional[ProjectConfig]: # Name expected by __init__.py
    """Retrieves the specific, merged configuration for a given project_id."""
    cfg = await get_config()
    project_specific_config = cfg["projects"].get(project_id)
    
    if not project_specific_config:
        return None

    # Example of merging (deep merge would be more complex):
    # For V0.1, we can assume project_specific_config contains full overrides if present.
    # A more robust merge would overlay project specifics onto global defaults for each section.
    # For now, this function returns the project's direct config entry.
    # The get_dag_rules_for_project, etc., below show more specific merging.
    return project_specific_config

async def get_all_project_configs() -> Dict[str, ProjectConfig]: # Name expected by __init__.py
    """Retrieves all project-specific configurations."""
    cfg = await get_config()
    return cfg["projects"]

async def get_dag_rules_for_project(project_id: str) -> DagRuleConfig:
    """Gets DAG rules for a project, merging global and project-specific."""
    cfg = await get_config()
    project_configs = cfg["projects"].get(project_id)
    
    # Start with a copy of global rules
    # Mypy might complain about direct update if TypedDicts are strictly typed.
    # Creating a new dict by merging is safer.
    current_rules: DagRuleConfig = cfg["global_dag_rules"].copy() # type: ignore

    if project_configs and project_configs.get("dag_rules"):
        project_specific_rules = project_configs["dag_rules"]
        # Update only keys present in project_specific_rules to override globals
        for key, value in project_specific_rules.items(): # type: ignore 
            if key in current_rules: # Check if key is a valid key for DagRuleConfig
                current_rules[key] = value # type: ignore
    return current_rules

async def get_task_weight(task_type: str, project_id: Optional[str] = None) -> Optional[TaskWeightConfig]:
    """Gets task weights, checking project-specific then global."""
    cfg = await get_config()
    if project_id:
        project_configs = cfg["projects"].get(project_id)
        if project_configs:
            project_task_cfg = project_configs.get("task_config")
            if project_task_cfg:
                project_task_weights = project_task_cfg.get("task_weights")
                if project_task_weights and task_type in project_task_weights:
                    return project_task_weights[task_type] # type: ignore
    return cfg["global_task_weights"].get(task_type)

async def get_clearance_policy(policy_name: str, project_id: Optional[str] = None) -> Optional[ClearancePolicyRule]:
    """Gets clearance policies, checking project-specific then global, then project default, then global default."""
    cfg = await get_config()
    if project_id:
        project_configs = cfg["projects"].get(project_id)
        if project_configs:
            project_policies = project_configs.get("clearance_policies")
            if project_policies:
                if policy_name in project_policies:
                    return project_policies[policy_name] # type: ignore
                # Fallback to project-specific default if policy_name not found in project_policies
                if project_policies.get("default"):
                    return project_policies["default"] # type: ignore
    
    # Fallback to global policy by name, then global default
    if policy_name in cfg["global_clearance_policies"]:
        return cfg["global_clearance_policies"][policy_name] # type: ignore
    return cfg["global_clearance_policies"].get("default")

# Ensure this file can be imported and its contents accessed as expected by core/__init__.py
