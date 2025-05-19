import json
import logging
import hashlib
from typing import Any, Dict, List, Optional

# --- Logger Configuration ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if not logger.hasHandlers():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)

# --- Utility Functions ---
def format_data_for_display(data: Any) -> str:
    """Formats data as a pretty JSON string or falls back to `str()`."""
    if isinstance(data, (dict, list, tuple)):
        try:
            return json.dumps(data, indent=2, sort_keys=True)
        except TypeError:
            logger.warning(f"Could not JSON serialize data of type {type(data)}, falling back to str().")
            return str(data)
    return str(data)

def hash_string(value: str) -> str:
    """Returns a SHA-256 hash of the given string."""
    return hashlib.sha256(value.encode()).hexdigest()

def read_json_file(filepath: str) -> Optional[Dict[str, Any]]:
    """Reads a JSON file and returns its contents as a dictionary."""
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"JSON file not found: {filepath}")
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON format in file: {filepath}")
    return None

def write_json_file(filepath: str, data: Dict[str, Any]) -> bool:
    """Writes data to a JSON file with proper formatting."""
    try:
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to write JSON file: {filepath} | Error: {e}")
        return False
