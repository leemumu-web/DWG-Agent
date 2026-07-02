from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


class AppHTTPException(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message, "details": details or {}})


def not_found(resource: str) -> AppHTTPException:
    return AppHTTPException(status.HTTP_404_NOT_FOUND, "NOT_FOUND", f"{resource} not found.")


def forbidden(message: str = "Permission denied.") -> AppHTTPException:
    return AppHTTPException(status.HTTP_403_FORBIDDEN, "FORBIDDEN", message)


def service_unavailable(code: str, message: str) -> AppHTTPException:
    return AppHTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, code, message)
