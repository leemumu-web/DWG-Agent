from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    bucket: str
    storage_key: str
    original_name: str
    file_ext: str
    content_type: str | None = None
    size_bytes: int
    sha256: str
    md5: str | None = None
    uploaded_by: int | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class DownloadUrlRead(BaseModel):
    url: str
    expires_in: int
