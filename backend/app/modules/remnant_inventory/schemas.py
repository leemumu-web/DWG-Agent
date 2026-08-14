from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SkipValidation,
    field_validator,
    model_validator,
)


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


class MaterialStatusUpdate(BaseModel):
    enabled: SkipValidation[bool]

    @model_validator(mode="before")
    @classmethod
    def defer_missing_field_validation(cls, value: object) -> object:
        # Keep the field required/boolean in OpenAPI while the route owns its Chinese error.
        if isinstance(value, dict) and "enabled" not in value:
            return {**value, "enabled": None}
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


class MaterialResolveCreate(BaseModel):
    code: str = Field(max_length=64)

    @field_validator("code")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class MaterialResolveCreateResult(BaseModel):
    material: MaterialRead
    created: bool


class ImportMaterialResolveCreate(BaseModel):
    code: SkipValidation[str]

    @model_validator(mode="before")
    @classmethod
    def defer_missing_field_validation(cls, value: object) -> object:
        # Keep a required string in OpenAPI while the route returns Chinese errors.
        if isinstance(value, dict) and "code" not in value:
            return {**value, "code": None}
        return value


class ImportItemUpdate(BaseModel):
    thickness_mm: Decimal | None = None
    material_id: int | None = None
    project_no: str | None = Field(default=None, max_length=128)
    project_no_secondary: str | None = Field(default=None, max_length=128)
    storage_location: str | None = Field(default=None, max_length=128)
    remark_1: str | None = Field(default=None, max_length=500)
    remark_2: str | None = Field(default=None, max_length=500)
    parts: list[str] | None = Field(default=None, max_length=500)


class BulkThicknessUpdate(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=1000)
    thickness_mm: Decimal


class BulkProjectUpdate(BaseModel):
    item_ids: SkipValidation[list[int]]
    project_no: SkipValidation[str]

    @model_validator(mode="before")
    @classmethod
    def defer_missing_field_validation(cls, value: object) -> object:
        # Keep required typed fields in OpenAPI while the route owns Chinese errors.
        if not isinstance(value, dict):
            return value
        return {
            **value,
            "item_ids": value.get("item_ids"),
            "project_no": value.get("project_no"),
        }


class BulkOptionalMetadataUpdate(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=1000)
    project_no_secondary: str | None = Field(default=None, max_length=128)
    storage_location: str | None = Field(default=None, max_length=128)
    remark_1: str | None = Field(default=None, max_length=500)
    remark_2: str | None = Field(default=None, max_length=500)


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
    # 乐观并发契约：必须提交调用方最近一次读取到的 Remnant.version；
    # 若已被其他操作修改则返回 409 REMNANT_STATE_CONFLICT，需重新读取后再试。
    version: int = Field(ge=1)


class RemnantUpdate(BaseModel):
    thickness_mm: Decimal | None = None
    material_id: int | None = None
    project_no: str | None = Field(default=None, max_length=128)
    project_no_secondary: str | None = Field(default=None, max_length=128)
    storage_location: str | None = Field(default=None, max_length=128)
    remark_1: str | None = Field(default=None, max_length=500)
    remark_2: str | None = Field(default=None, max_length=500)
    parts: list[str] | None = Field(default=None, max_length=500)


class RemnantBulkArchiveRequest(BaseModel):
    remnant_ids: list[int] = Field(min_length=1, max_length=200)


class RemnantBulkArchiveFailure(BaseModel):
    remnant_id: int
    code: str
    message: str


class RemnantBulkArchiveResult(BaseModel):
    archived: list[int]
    failed: list[RemnantBulkArchiveFailure]
