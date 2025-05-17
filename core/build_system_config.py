# ========================================
# 📁 core/build_system_config.py
# ========================================
import logging
import asyncio # For asyncio.Lock and async functions
from typing import Dict, Any, List, TypedDict, Optional, Literal

logger = logging.getLogger(__name__)

# --- TypedDicts for Configuration Clarity ---

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

class ProjectSpecificTaskConfig(TypedDict):
    task_weights: Optional[Dict[str, TaskWeightConfig]]
    task_parameters: Optional[Dict[str, Dict[str, Any]]] 

class ProjectConfig(TypedDict):
    description: Optional[str]
    dag_rules: Optional[DagRuleConfig] 
    task_config: Optional[ProjectSpecificTaskConfig]
    clearance_policies: Optional[ClearancePoliciesConfig] 

class BuildSystemConfig(TypedDict):
    global_dag_rules: DagRuleConfig
    global_task_weights: Dict[str, TaskWeightConfig] 
    global_clearance_policies: ClearancePoliciesConfig
    projects: Dict[str, ProjectConfig] 

# --- Default Configuration Data (V0.1 - Hardcoded) ---
_DEFAULT_CONFIG: BuildSystemConfig = {
    "global_dag_rules": {
        "allow_cycles": False,
        "max_depth": 50,
        "default_timeout_seconds": 3 * 60 * 60 # 3 hours
    },
    "global_task_weights": {
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
    "global_clearance_policies": {
        "default": {"required_checks": [], "min_approvals": 0, "auto_approve_on_success": True},
        "production_deployment": {
            "required_checks": ["all_tests_passed", "security_scan_clean", "manual_approval_product_owner"],
            "min_approvals": 1,
            "auto_approve_on_success": False
        }
    },
    "projects": {
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
            "description": "An example agent project configuration"
        }
    }
}

# --- Configuration Access Functions ---

_loaded_config: Optional[BuildSystemConfig] = None
_config_lock = asyncio.Lock() # For thread-safe/async-safe lazy loading

def _load_config_from_source() -> BuildSystemConfig:
    """
    Loads configuration. For V0.1, it's hardcoded.
    Future: Load from a JSON/YAML file. If so, 'import os' and 'import json' would be needed here.
    """
    logger.debug("Using hardcoded default build system configuration for V0.1.")
    return _DEFAULT_CONFIG

async def get_config() -> BuildSystemConfig:
    """Async function to get the loaded configuration, ensuring it's loaded once."""
    global _loaded_config
    if _loaded_config is None:
        async with _config_lock:
            if _loaded_config is None: # Double check after acquiring lock
                _loaded_config = _load_config_from_source()
    return _loaded_config

async def get_project_config(project_id: str) -> Optional[ProjectConfig]:
    cfg = await get_config()
    return cfg["projects"].get(project_id)

async def get_dag_rules_for_project(project_id: str) -> DagRuleConfig:
    cfg = await get_config()
    project_cfg = cfg["projects"].get(project_id)
    
    current_rules = cfg["global_dag_rules"].copy() # Start with a copy of global rules

    if project_cfg and project_cfg.get("dag_rules"):
        project_specific_rules = project_cfg["dag_rules"]
        # Update only keys present in project_specific_rules to override globals
        for key, value in project_specific_rules.items(): # type: ignore 
            # Ensure key is valid for DagRuleConfig if strict type checking is later enforced on merged_rules
            if key in current_rules: # type: ignore 
                current_rules[key] = value # type: ignore
    return current_rules

async def get_task_weight(task_type: str, project_id: Optional[str] = None) -> Optional[TaskWeightConfig]:
    cfg = await get_config()
    if project_id:
        project_cfg = cfg["projects"].get(project_id)
        if project_cfg:
            project_task_cfg = project_cfg.get("task_config")
            if project_task_cfg:
                project_task_weights = project_task_cfg.get("task_weights")
                if project_task_weights and task_type in project_task_weights:
                    return project_task_weights[task_type]
    return cfg["global_task_weights"].get(task_type)

async def get_clearance_policy(policy_name: str, project_id: Optional[str] = None) -> Optional[ClearancePolicyRule]:
    cfg = await get_config()
    if project_id:
        project_cfg = cfg["projects"].get(project_id)
        if project_cfg:
            project_policies = project_cfg.get("clearance_policies")
            if project_policies:
                if policy_name in project_policies:
                    return project_policies[policy_name]
                if project_policies.get("default"):
                    return project_policies["default"]
    
    if policy_name in cfg["global_clearance_policies"]:
        return cfg["global_clearance_policies"][policy_name]
    return cfg["global_clearance_policies"].get("default")

# --- Illustrative usage (not for direct execution from this module) ---
# An agent or service would import and use these functions like so:
#
# import asyncio
# from core.build_system_config import get_project_config
#
# async def example_function():
#     config = await get_project_config("forgeiq_backend_py")
#     if config:
#         print(config.get("description"))
#
# # To run: asyncio.run(example_function())
