from __future__ import annotations

from datetime import datetime

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
    created_at: datetime
    updated_at: datetime
