from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    deleted_at: datetime | None = None
    purged_at: datetime | None = None
    batch_name: str | None = None
    created_at: datetime
    updated_at: datetime


class DownloadUrlRead(BaseModel):
    url: str
    expires_in: int


class DxfPreviewBoundsRead(BaseModel):
    min_x: float
    min_y: float
    max_x: float
    max_y: float


class DxfPreviewRead(BaseModel):
    file_id: int
    file_name: str
    preview_file_id: int
    content_url: str
    content_type: str
    document_entities: int
    modelspace_entities: int
    entity_counts: dict[str, int]
    layers: list[str]
    layer_colors: dict[str, int]
    bounds: DxfPreviewBoundsRead
    cached: bool


class BulkDeleteRequest(BaseModel):
    file_ids: list[int]


class BatchBulkDeleteRequest(BaseModel):
    batch_names: list[str] = Field(min_length=1, max_length=100)

    @field_validator("batch_names")
    @classmethod
    def validate_batch_names(cls, names: list[str]) -> list[str]:
        cleaned = [name.strip() for name in names]
        if any(not name for name in cleaned):
            raise ValueError("batch_names must not contain blank names")
        if any(len(name) > 128 for name in cleaned):
            raise ValueError("batch_names must not exceed 128 characters")
        return cleaned


class BatchBulkDeleteResult(BaseModel):
    deleted_batch_count: int = Field(ge=0)
    deleted_file_count: int = Field(ge=0)
    cancelled_job_count: int = Field(ge=0)


class ZipDownloadRequest(BaseModel):
    file_ids: list[int]
    formats: list[str]  # each element is "dwg" or "dxf"
    folder_name: str = "图纸导出"


class ZipFormatAvailability(BaseModel):
    format: str
    available_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    missing_file_ids: list[int]
    complete: bool


class ZipAvailabilityPreview(BaseModel):
    file_count: int = Field(ge=0)
    formats: list[ZipFormatAvailability]
    can_download: bool


class ZipUploadResult(BaseModel):
    batch_name: str
    files: list[FileRead]
    success_count: int
    skipped_count: int
