from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RemediationPreview(BaseModel):
    action: str
    finding_ids: list[int]
    count: int
    total_bytes: int
    risk: str
    expires_at: datetime
    confirmation_word: str | None = None
    token: str


class RemediationResult(BaseModel):
    transfer_uid: str
    action: str
    status: str
    count: int
    file_ids: list[int]

