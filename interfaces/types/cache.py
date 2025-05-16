# interfaces/types/cache.py
from typing import TypedDict, Optional, Dict, Any, List

class CacheKeyParams(TypedDict):
    """Parameters used to generate a cache key/fingerprint."""
    project_id: str
    task_id: str # Unique identifier for the task definition (e.g., node_id from DAG)
    task_type: str
    # Hashes of input files/directories, sorted
    input_file_hashes: Dict[str, str] # e.g., {"src/main.py": "hash123", "config.json": "hash456"}
    # Relevant configuration parameters that affect the task output, sorted
    task_parameters: Dict[str, Any] 
    # Version of the tool/runner if it affects output
    tool_version: Optional[str] 

class CachedItemMetadata(TypedDict):
    """Metadata stored alongside the cached result."""
    cache_key: str
    created_at: str # ISO datetime
    task_id: str
    project_id: str
    # For V0.1, we'll focus on caching JSON-serializable results.
    # For binary artifacts, this would point to an artifact store (e.g., S3 URL)
    # artifact_location: Optional[str] 
    result_type: str # e.g., "json_dict", "text_log", "file_path_pointer"

class CacheGetResponse(TypedDict):
    is_hit: bool
    cache_key: Optional[str]
    metadata: Optional[CachedItemMetadata]
    cached_result: Optional[Any] # The actual cached data (e.g., a dict, string)

class CacheStoreRequest(TypedDict):
    cache_key: str # The pre-computed key based on CacheKeyParams
    project_id: str
    task_id: str
    result_to_cache: Any # JSON-serializable result for V0.1
    result_type: str # e.g., "json_dict"
    # artifact_to_store_path: Optional[str] # Path to a local artifact to upload

class CacheStoreResponse(TypedDict):
    success: bool
    cache_key: str
    message: Optional[str]
