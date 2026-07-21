from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    actor_user_id: int | None = None
    action: str
    resource_type: str
    resource_id: int | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    before_json: dict[str, Any] | None = None
    after_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
