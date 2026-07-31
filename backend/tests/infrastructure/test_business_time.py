from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.modules.automation.agent import memory as agent_memory
from app.modules.operations.control_plane.service import _is_before
from app.modules.remnant_inventory.export import _excel_datetime
from app.modules.workflows import batch_exports, retention
from app.modules.projects.models.project import Project
from app.modules.projects.schemas.project import ProjectRead
from app.platform.http.envelopes import meta
from app.platform.http.exceptions import AppHTTPException
from app.platform.time import BUSINESS_TIMEZONE, as_business_time, business_now


def test_business_now_is_explicit_beijing_time():
    value = business_now()

    assert value.tzinfo is BUSINESS_TIMEZONE
    assert value.utcoffset() is not None
    assert value.utcoffset().total_seconds() == 8 * 60 * 60


def test_naive_database_value_is_interpreted_as_beijing_wall_time():
    value = as_business_time(datetime(2026, 8, 1, 0, 30, 0))

    assert value.tzinfo is BUSINESS_TIMEZONE
    assert value.isoformat() == "2026-08-01T00:30:00+08:00"


def test_loaded_orm_datetimes_serialize_with_explicit_beijing_offset(db: Session):
    project = Project(code="timezone-project", name="时区项目", status="active")
    db.add(project)
    db.commit()
    project_id = project.id
    db.expunge_all()

    loaded = db.get(Project, project_id)

    assert loaded is not None
    assert loaded.created_at.tzinfo is BUSINESS_TIMEZONE
    assert '"created_at":"' in ProjectRead.model_validate(loaded).model_dump_json()
    assert "+08:00" in ProjectRead.model_validate(loaded).model_dump_json()
    assert db.is_modified(loaded) is False


def test_http_envelope_timestamp_has_explicit_beijing_offset():
    timestamp = meta("timezone-request")["timestamp"]

    assert timestamp.endswith("+08:00")


def test_naive_database_cutoffs_are_compared_as_beijing_wall_time(monkeypatch):
    now = datetime(2026, 8, 1, 9, 0, tzinfo=BUSINESS_TIMEZONE)
    monkeypatch.setattr(agent_memory, "business_now", lambda: now)
    monkeypatch.setattr(agent_memory.settings, "agent_memory_ttl", 60 * 60)

    expired = SimpleNamespace(updated_at=datetime(2026, 8, 1, 7, 30))

    assert agent_memory._is_expired(expired) is True
    assert _is_before(datetime(2026, 8, 1, 8, 0), now) is True


@pytest.mark.parametrize("module", [batch_exports, retention])
def test_naive_download_expiry_is_interpreted_as_beijing_wall_time(monkeypatch, module):
    token = "timezone-token"
    row = SimpleNamespace(
        token_digest=hashlib.sha256(token.encode()).hexdigest(),
        token_expires_at=datetime(2026, 8, 1, 8, 0),
    )
    monkeypatch.setattr(
        module,
        "business_now",
        lambda: datetime(2026, 8, 1, 9, 0, tzinfo=BUSINESS_TIMEZONE),
    )
    checker = (
        module.require_export_token
        if module is batch_exports
        else module.require_retention_token
    )

    with pytest.raises(AppHTTPException) as caught:
        checker(row, token)

    assert caught.value.status_code == 410


def test_naive_excel_datetime_remains_same_beijing_wall_time():
    value = datetime(2026, 8, 1, 8, 15)

    assert _excel_datetime(value) == value


def test_aware_absolute_time_converts_without_changing_the_instant():
    value = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)

    converted = as_business_time(value)

    assert converted.isoformat() == "2026-08-01T08:00:00+08:00"
    assert converted.timestamp() == value.timestamp()


def test_data_overview_business_day_starts_at_beijing_midnight():
    from app.modules.operations.data_catalog.queries import _business_day_start

    start = _business_day_start(datetime(2026, 8, 1, 13, 30, tzinfo=UTC))

    assert start.isoformat() == "2026-08-01T00:00:00+08:00"


def test_celery_scheduling_uses_business_timezone_and_preserves_utc_protocol():
    from app.platform.messaging.celery_app import celery_app

    assert celery_app.conf.timezone == "Asia/Shanghai"
    assert celery_app.conf.enable_utc is True
