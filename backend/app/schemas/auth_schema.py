from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.schemas.user_schema import UserRead

# Re-use the same common-password set kept in user_schema.
_COMMON_PASSWORDS = frozenset({
    "password", "password123", "admin123", "admin123456", "12345678",
    "123456789", "qwerty123", "abc12345", "letmein12", "welcome123",
    "changeme12", "pass1234", "pass12345", "pass123456",
    "Password123", "Admin123456", "Qwerty1234",
})


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserRead


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(
        min_length=12,
        description="Password — minimum 12 characters, must contain upper + lower + digit.",
    )

    @field_validator("new_password")
    @classmethod
    def _enforce_password_complexity(cls, v: str) -> str:
        if v.lower() in _COMMON_PASSWORDS:
            raise ValueError("This password is too common — choose a stronger one.")
        if not (any(c.islower() for c in v) and any(c.isupper() for c in v) and any(c.isdigit() for c in v)):
            raise ValueError(
                "Password must contain at least one uppercase letter, "
                "one lowercase letter, and one digit."
            )
        return v
