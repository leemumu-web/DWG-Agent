from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MaterialCreate(BaseModel):
    code: str = Field(max_length=64)
    family_code: str = Field(max_length=64)

    @field_validator("code", "family_code")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class MaterialUpdate(BaseModel):
    family_code: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None

    @field_validator("family_code")
    @classmethod
    def reject_blank_family(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value


class MaterialAliasReplace(BaseModel):
    aliases: list[str] = Field(default_factory=list, max_length=200)


class MaterialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    family_code: str
    enabled: bool
    aliases: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ImportItemUpdate(BaseModel):
    thickness_mm: Decimal | None = None
    material_id: int | None = None
    project_no: str | None = Field(default=None, max_length=128)
    parts: list[str] | None = Field(default=None, max_length=500)


class BulkThicknessUpdate(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=1000)
    thickness_mm: Decimal


class ImportConfirmRequest(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=1000)


class ImportConfirmationEntry(BaseModel):
    item_id: int
    remnant_id: int | None = None
    code: str | None = None


class ImportConfirmationResult(BaseModel):
    confirmed: list[ImportConfirmationEntry] = Field(default_factory=list)
    invalid: list[ImportConfirmationEntry] = Field(default_factory=list)
    already_confirmed: list[ImportConfirmationEntry] = Field(default_factory=list)


class RemnantReserveRequest(BaseModel):
    version: int = Field(ge=1)


class RemnantUpdate(BaseModel):
    thickness_mm: Decimal | None = None
    material_id: int | None = None
    project_no: str | None = Field(default=None, max_length=128)
    parts: list[str] | None = Field(default=None, max_length=500)
