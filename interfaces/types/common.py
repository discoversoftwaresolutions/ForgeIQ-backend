# ==================================
# 📁 interfaces/types/common.py
# ==================================
from typing import TypedDict, Literal # Use Literal for specific string sets

Status = Literal['PENDING', 'QUEUED', 'RUNNING', 'COMPLETED_SUCCESS', 'SUCCESS', 
                 'FAILED', 'CANCELLED', 'SKIPPED', 'UNKNOWN', 
                 'COMPLETED_WITH_FINDINGS', 'COMPLETED_CLEAN', 'COMPLETED_PARTIAL',
                 'ACTIVE', 'INACTIVE', 'DEGRADED', 'INITIALIZING', # For agent status
                 'NOT_FOUND', 'API_ERROR', 'POLL_ERROR', 'TIMEOUT'] # For query/process status

class Timestamped(TypedDict):
    created_at: str  # ISO 8601 datetime string
    updated_at: str  # ISO 8601 datetime string
