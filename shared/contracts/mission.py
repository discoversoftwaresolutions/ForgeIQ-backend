from typing import Dict, Any, List, Literal, Optional
from pydantic import BaseModel, Field
from datetime import datetime


class MissionContext(BaseModel):
    mission_id: str = Field(..., description="Globally unique mission identifier")
    originating_system: Literal["forgeiq", "external_api"]
    user_intent: str
    constraints: Dict[str, Any]
    compliance_profiles: List[str]
    risk_tolerance: Literal["low", "medium", "high"]
    budget_ceiling: Optional[str]
    environment: Literal["commercial", "govcloud", "airgapped"]
    created_at: datetime
