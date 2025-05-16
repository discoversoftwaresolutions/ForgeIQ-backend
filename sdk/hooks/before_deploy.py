# ==================================
# 📁 sdk/hooks/before_deploy.py
# ==================================
import logging
from typing import Protocol, Dict, Any, List, Callable, Awaitable

logger = logging.getLogger(__name__)

# Define a type for the hook context
class DeployContext(Dict[str, Any]): # Using Dict for simplicity, Pydantic BaseModel could be used
    pass

# Define the hook function signature using Protocol for structural subtyping
class BeforeDeployCallable(Protocol):
    async def __call__(self, context: DeployContext) -> bool: # Return True to proceed, False to halt
        ...

class HookManager:
    def __init__(self):
        self._before_deploy_hooks: List[BeforeDeployCallable] = []
        logger.info("HookManager initialized.")

    def register_before_deploy_hook(self, hook: BeforeDeployCallable):
        """Registers a function to be called before a deployment is triggered."""
        if not callable(hook):
            logger.error("Failed to register before_deploy_hook: hook is not callable.")
            raise TypeError("Hook must be a callable (async function).")

        # Basic check for async callable (more robust checks might be needed)
        import inspect
        if not inspect.iscoroutinefunction(hook):
             logger.error("Failed to register before_deploy_hook: hook must be an async function.")
             raise TypeError("Hook must be an async function (defined with async def).")

        self._before_deploy_hooks.append(hook)
        logger.info(f"Registered before_deploy_hook: {getattr(hook, '__name__', 'anonymous_hook')}")

    async def execute_before_deploy_hooks(self, context: DeployContext) -> bool:
        """
        Executes all registered before-deploy hooks.
        Returns False if any hook returns False, otherwise True.
        """
        if not self._before_deploy_hooks:
            return True # No hooks to run, proceed

        logger.info(f"Executing {len(self._before_deploy_hooks)} before_deploy hook(s)...")
        span = None # Placeholder for potential OTel span
        # try:
        #     from opentelemetry import trace
        #     tracer = trace.get_tracer("forgeiq_sdk.hooks")
        #     span = tracer.start_span("execute_before_deploy_hooks")
        #     span.set_attribute("hooks.count", len(self._before_deploy_hooks))
        # except ImportError:
        #     pass

        all_hooks_passed = True
        for hook in self._before_deploy_hooks:
            try:
                hook_name = getattr(hook, '__name__', 'anonymous_hook')
                logger.debug(f"Executing before_deploy hook: {hook_name}")
                # if span: span.add_event(f"Executing hook: {hook_name}")

                proceed = await hook(context) # Await the async hook

                if not isinstance(proceed, bool):
                    logger.warning(f"Before_deploy hook '{hook_name}' did not return a boolean. Defaulting to proceed=True.")
                    proceed = True # Or False, depending on desired strictness

                if not proceed:
                    all_hooks_passed = False
                    logger.warning(f"Before_deploy hook '{hook_name}' returned False. Halting deployment.")
                    # if span: span.set_attribute(f"hook.{hook_name}.result", "halted"); span.set_attribute("hooks.overall_result", "halted")
                    break 
                # if span: span.set_attribute(f"hook.{hook_name}.result", "proceed")
            except Exception as e:
                logger.error(f"Error executing before_deploy hook '{getattr(hook, '__name__', 'anonymous_hook')}': {e}", exc_info=True)
                # if span: span.record_exception(e); span.set_attribute("hooks.error", True)
                all_hooks_passed = False # Halt on error
                break 

        # if span: 
        #     if all_hooks_passed: span.set_attribute("hooks.overall_result", "proceed")
        #     span.end()
        return all_hooks_passed

# Example of how HookManager might be integrated into ForgeIQClient (conceptual)
# In sdk/client.py, ForgeIQClient would have a HookManager instance:
#
# class ForgeIQClient:
#     def __init__(self, ..., hook_manager: Optional[HookManager] = None):
#         # ...
#         self.hooks = hook_manager or HookManager()
#
#     async def trigger_deployment(self, ...):
#         context = DeployContext(project_id=project_id, service_name=service_name, ...)
#         if not await self.hooks.execute_before_deploy_hooks(context):
#             logger.info("Deployment halted by before_deploy hook.")
#             raise ForgeIQSDKError("Deployment halted by a pre-deployment hook.", status_code=403) # Or a specific exception
#
#         # ... proceed with actual API call to trigger deployment ...

# How a user of the SDK might register a hook:
#
# from forgeiq_sdk import ForgeIQClient, HookManager
# from forgeiq_sdk.hooks.before_deploy import DeployContext
#
# async def my_custom_pre_deploy_check(context: DeployContext) -> bool:
#     print(f"Running custom check for project: {context.get('project_id')}")
#     # Perform some validation...
#     if context.get('target_environment') == 'production' and not context.get('is_critical_patch'):
#         print("Custom check: Non-critical patch to production requires extra validation (failing for demo).")
#         return False
#     return True
#
# hook_manager = HookManager()
# hook_manager.register_before_deploy_hook(my_custom_pre_deploy_check)
#
# client = ForgeIQClient(base_url="...", hook_manager=hook_manager)
# # Now, when client.trigger_deployment is called, my_custom_pre_deploy_check will run first.
