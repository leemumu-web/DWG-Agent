"""Endpoint-scoped idempotency-key validation for Excel commands."""

from __future__ import annotations

import re

from app.platform.http.exceptions import AppHTTPException


def scoped_request_key(endpoint: str, value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 96
        or re.fullmatch(r"[A-Za-z0-9._:-]+", normalized) is None
    ):
        raise AppHTTPException(
            422,
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key has an invalid format.",
        )
    return f"{endpoint}:{normalized}"


__all__ = ["scoped_request_key"]
