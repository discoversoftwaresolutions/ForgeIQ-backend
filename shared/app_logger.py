# ============================
# 📁 shared/app_logger.py
# ============================
import logging
import os
import sys # For logging.StreamHandler(sys.stderr)
from typing import Dict, Optional # For type hints

_loggers: Dict[str, logging.Logger] = {} # Cache for logger instances

def get_app_logger(name: str, service_name_override: Optional[str] = None) -> logging.Logger:
    """
    Retrieves or creates a standardized logger instance.
    The logger name will be 'service_name.name' if service_name is provided.
    OpenTelemetry LoggingInstrumentor (if active) will enrich logs with trace context.
    """

    # Determine the effective service name for the logger's full name and format string
    # This helps distinguish logs when multiple services might use this shared logger.
    effective_service_name = service_name_override or os.getenv("SERVICE_NAME", "DefaultApp")

    # Use a combined name for uniqueness in the logging hierarchy if desired
    logger_full_name = f"{effective_service_name}.{name}" if name != effective_service_name else effective_service_name


    if logger_full_name in _loggers:
        return _loggers[logger_full_name]

    logger_instance = logging.getLogger(logger_full_name)

    # Configure only if this specific logger instance hasn't been configured by this function before
    # (checking handlers might not be enough if root logger is configured aggressively)
    # A simple check: if it's at default level (WARN) and has no handlers, configure it.
    # Or, more simply, always try to set level and add handler if none.

    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    numeric_log_level = getattr(logging, log_level_str, logging.INFO)

    # Set level on this specific logger. If root logger has a stricter level, that might still apply.
    logger_instance.setLevel(numeric_log_level)

    # Add our standard handler ONLY if no handlers are already configured for this logger
    # This avoids duplicate log messages if basicConfig was called earlier for the root logger.
    if not logger_instance.handlers:
        handler = logging.StreamHandler(sys.stderr) # Log to stderr by default
        # This format string includes the effective service name.
        # OTel LoggingInstrumentor will add trace/span IDs if OTel is active.
        formatter = logging.Formatter(
            f'%(asctime)s - %(levelname)-8s - [{effective_service_name}] - %(name)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger_instance.addHandler(handler)

        # Prevent messages from propagating to the root logger if we've added our own handler
        # This is important to avoid duplicate messages if the root logger also has handlers.
        logger_instance.propagate = False 

    # If basicConfig was already called and configured the root logger,
    # this logger will inherit that level if its own level is not more specific.
    # We ensure our desired level is set.
    if logger_instance.level > numeric_log_level :
         logger_instance.setLevel(numeric_log_level)


    _loggers[logger_full_name] = logger_instance
    return logger_instance
