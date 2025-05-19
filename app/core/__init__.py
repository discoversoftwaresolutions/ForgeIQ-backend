import logging  # ✅ Standardized import placement

# ✅ Ensure proper import from `main_orchestrator`
try:
    from core.orchestrator.main_orchestrator import Orchestrator, OrchestrationError  # 🔹 Updated absolute import path
except ModuleNotFoundError as e:
    logging.error(f"Orchestrator module could not be imported: {e}", exc_info=True)
    Orchestrator = None  # type: ignore
    OrchestrationError = None  # type: ignore

__all__ = ["Orchestrator", "OrchestrationError"]

# ✅ Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if not logger.hasHandlers():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)

logger.info("Initialized core.orchestrator package.")
