"""Authenticated native MySQL structure and row administration."""

from __future__ import annotations

import base64
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import MetaData, Table, and_, delete, func, insert, inspect, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.sql.schema import Column

from app.modules.identity.interface import CurrentUser, User, is_admin
from app.modules.operations.audit.interface import write_audit_log
from app.platform.http.dependencies import DbSession
from app.platform.http.envelopes import ok, page
from app.platform.http.exceptions import AppHTTPException, forbidden, not_found

router = APIRouter()
SENSITIVE_MARKERS = (
    "password",
    "secret",
    "credential",
    "access_token",
    "refresh_token",
    "token_hash",
    "api_key",
)
READ_ONLY_IDENTITY_TABLES = frozenset(
    {
        "sys_users",
        "sys_roles",
        "sys_user_roles",
        "sys_permissions",
        "sys_role_permissions",
        "token_blacklist",
    }
)


class RowCreate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class RowUpdate(BaseModel):
    primary_key: dict[str, Any]
    values: dict[str, Any] = Field(default_factory=dict)


class RowDelete(BaseModel):
    primary_key: dict[str, Any]


def _table(db: DbSession, table_name: str) -> Table:
    bind = db.get_bind()
    if table_name not in inspect(bind).get_table_names():
        raise not_found("Database table")
    return Table(table_name, MetaData(), autoload_with=bind)


def _is_sensitive(name: str) -> bool:
    normalized = name.casefold()
    return any(marker in normalized for marker in SENSITIVE_MARKERS)


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"base64:{base64.b64encode(value).decode('ascii')}"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def _row_data(row: Any) -> dict[str, Any]:
    return {
        key: "••••••" if _is_sensitive(key) and value is not None else _json_value(value)
        for key, value in row._mapping.items()
    }


def _column_data(column: Column[Any]) -> dict[str, Any]:
    return {
        "name": column.name,
        "type": str(column.type),
        "nullable": column.nullable,
        "primary_key": column.primary_key,
        "autoincrement": bool(column.autoincrement is True),
        "default": str(column.default.arg) if column.default is not None else None,
        "sensitive": _is_sensitive(column.name),
        "required": (
            not column.nullable
            and not column.primary_key
            and column.default is None
            and column.server_default is None
        ),
    }


def _coerce_value(column: Column[Any], value: Any) -> Any:
    if value is None:
        return None
    try:
        python_type = column.type.python_type
    except (AttributeError, NotImplementedError):
        return value
    if isinstance(value, python_type):
        return value
    if python_type is datetime and isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if python_type is date and isinstance(value, str):
        return date.fromisoformat(value)
    if python_type is time and isinstance(value, str):
        return time.fromisoformat(value)
    if python_type is bool and isinstance(value, str):
        return value.casefold() in {"1", "true", "yes", "on"}
    if python_type in {int, float, str, Decimal}:
        return python_type(value)
    return value


def _validated_values(
    table: Table,
    values: dict[str, Any],
    *,
    for_create: bool,
) -> dict[str, Any]:
    columns = {column.name: column for column in table.columns}
    unknown = sorted(set(values) - set(columns))
    if unknown:
        raise AppHTTPException(
            422,
            "UNKNOWN_DATABASE_COLUMNS",
            "One or more database columns do not exist.",
            {"columns": unknown},
        )
    protected = sorted(
        name
        for name in values
        if (
            columns[name].primary_key
            or columns[name].autoincrement is True
            or _is_sensitive(name)
        )
    )
    if protected:
        raise AppHTTPException(
            422,
            "PROTECTED_DATABASE_COLUMNS",
            "Primary-key, auto-increment, and sensitive columns cannot be changed here.",
            {"columns": protected},
        )
    normalized: dict[str, Any] = {}
    for name, value in values.items():
        try:
            normalized[name] = _coerce_value(columns[name], value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise AppHTTPException(
                422,
                "INVALID_DATABASE_VALUE",
                "A database value does not match its column type.",
                {"column": name, "type": str(columns[name].type)},
            ) from exc
    now = datetime.now().astimezone()
    if for_create and "created_at" in columns and "created_at" not in normalized:
        normalized["created_at"] = now
    if "updated_at" in columns and "updated_at" not in normalized:
        normalized["updated_at"] = now
    return normalized


def _primary_key_clause(table: Table, values: dict[str, Any]):
    names = [column.name for column in table.primary_key.columns]
    if not names:
        raise AppHTTPException(
            409,
            "TABLE_HAS_NO_PRIMARY_KEY",
            "Rows in this table cannot be changed because it has no primary key.",
        )
    if set(values) != set(names):
        raise AppHTTPException(
            422,
            "INVALID_PRIMARY_KEY",
            "The complete primary key is required.",
            {"expected": names},
        )
    return and_(*(table.c[name] == values[name] for name in names))


def _require_admin(user: User) -> None:
    if not is_admin(user):
        raise forbidden("Only administrators can change database rows.")


def _require_writable_table(table: Table) -> None:
    if table.name in READ_ONLY_IDENTITY_TABLES:
        raise AppHTTPException(
            409,
            "IDENTITY_TABLE_READ_ONLY",
            "Identity and access-control tables are read-only in the database console.",
            {"table": table.name},
        )


def _database_error(exc: SQLAlchemyError) -> AppHTTPException:
    if isinstance(exc, IntegrityError):
        return AppHTTPException(
            409,
            "DATABASE_CONSTRAINT_VIOLATION",
            "The row conflicts with a database constraint.",
        )
    return AppHTTPException(
        422,
        "DATABASE_ROW_INVALID",
        "The database rejected the supplied row values.",
    )


@router.get("/mysql/tables")
def list_mysql_tables(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
):
    _require_admin(current_user)
    inspector = inspect(db.get_bind())
    rows = []
    for name in sorted(inspector.get_table_names()):
        columns = inspector.get_columns(name)
        rows.append(
            {
                "name": name,
                "column_count": len(columns),
                "primary_key": inspector.get_pk_constraint(name).get(
                    "constrained_columns", []
                ),
                "writable": name not in READ_ONLY_IDENTITY_TABLES,
            }
        )
    return ok(rows, request.state.request_id)


@router.get("/mysql/tables/{table_name}")
def get_mysql_table(
    table_name: str,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
):
    _require_admin(current_user)
    table = _table(db, table_name)
    total = db.scalar(select(func.count()).select_from(table)) or 0
    return ok(
        {
            "name": table.name,
            "row_count": total,
            "primary_key": [column.name for column in table.primary_key.columns],
            "columns": [_column_data(column) for column in table.columns],
            "writable": table.name not in READ_ONLY_IDENTITY_TABLES,
        },
        request.state.request_id,
    )


@router.get("/mysql/tables/{table_name}/rows")
def list_mysql_rows(
    table_name: str,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    page_no: int = Query(1, alias="page", ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    _require_admin(current_user)
    table = _table(db, table_name)
    total = db.scalar(select(func.count()).select_from(table)) or 0
    ordering = list(table.primary_key.columns) or list(table.columns)[:1]
    statement = (
        select(table)
        .order_by(*ordering)
        .offset((page_no - 1) * page_size)
        .limit(page_size)
    )
    rows = db.execute(statement).all()
    return page(
        [_row_data(row) for row in rows],
        page_no,
        page_size,
        total,
        request.state.request_id,
    )


@router.post("/mysql/tables/{table_name}/rows", status_code=status.HTTP_201_CREATED)
def create_mysql_row(
    table_name: str,
    payload: RowCreate,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
):
    _require_admin(current_user)
    table = _table(db, table_name)
    _require_writable_table(table)
    values = _validated_values(table, payload.values, for_create=True)
    try:
        result = db.execute(insert(table).values(**values))
        primary_key = dict(
            zip(
                [column.name for column in table.primary_key.columns],
                result.inserted_primary_key,
                strict=False,
            )
        )
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="database_row.create",
            resource_type=table.name,
            resource_id=primary_key.get("id"),
            after_json={"primary_key": primary_key},
            request=request,
        )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise _database_error(exc) from exc
    return ok({"primary_key": primary_key}, request.state.request_id)


@router.patch("/mysql/tables/{table_name}/rows")
def update_mysql_row(
    table_name: str,
    payload: RowUpdate,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
):
    _require_admin(current_user)
    table = _table(db, table_name)
    _require_writable_table(table)
    values = _validated_values(table, payload.values, for_create=False)
    clause = _primary_key_clause(table, payload.primary_key)
    before = db.execute(select(table).where(clause)).first()
    if before is None:
        raise not_found("Database row")
    try:
        db.execute(update(table).where(clause).values(**values))
        after = db.execute(select(table).where(clause)).first()
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="database_row.update",
            resource_type=table.name,
            resource_id=payload.primary_key.get("id"),
            before_json=_row_data(before),
            after_json=_row_data(after),
            request=request,
        )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise _database_error(exc) from exc
    return ok(_row_data(after), request.state.request_id)


@router.delete("/mysql/tables/{table_name}/rows")
def delete_mysql_row(
    table_name: str,
    payload: RowDelete,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
):
    _require_admin(current_user)
    table = _table(db, table_name)
    _require_writable_table(table)
    clause = _primary_key_clause(table, payload.primary_key)
    before = db.execute(select(table).where(clause)).first()
    if before is None:
        raise not_found("Database row")
    try:
        db.execute(delete(table).where(clause))
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="database_row.delete",
            resource_type=table.name,
            resource_id=payload.primary_key.get("id"),
            before_json=_row_data(before),
            request=request,
        )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise _database_error(exc) from exc
    return ok({"deleted": True}, request.state.request_id)


__all__ = ["router"]
