# =========================================
#   shared/utils/example_util.py
# =========================================
import json # Standard library import at the top
from typing import Any

# Example of using the shared logger within another shared utility:
# from ..app_logger import get_app_logger 
# logger = get_app_logger(__name__, service_name_override="SharedUtils")

def format_data_for_display(data: Any) -> str:
    """A simple utility to format data as a pretty JSON string or fallback to str()."""
    if isinstance(data, (dict, list, tuple)): # tuple also included
        try:
            return json.dumps(data, indent=2, sort_keys=True)
        except TypeError:
            # logger.warning(f"Could not JSON serialize data of type {type(data)}, falling back to str().")
            return str(data) 
    return str(data)
