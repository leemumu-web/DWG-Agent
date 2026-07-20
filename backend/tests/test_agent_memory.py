"""Tests for the MySQL-backed agent memory service."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.agent_memory import AgentMemory
from app.platform.config.settings import settings
from app.services.agent_memory import (
    append_and_save,
    delete_session_history,
    get_session_history,
    save_session_history,
)


class TestGetSessionHistory:
    def test_empty_for_unknown_session(self, db: Session):
        assert get_session_history(db, "no-such-session") == []

    def test_returns_stored_messages(self, db: Session):
        msgs = [{"role": "user", "content": "hello"}]
        save_session_history(db, "s1", msgs)
        assert get_session_history(db, "s1") == msgs

    def test_empty_list_stored_and_retrieved(self, db: Session):
        save_session_history(db, "empty", [])
        assert get_session_history(db, "empty") == []

    def test_expired_session_returns_empty_and_cleans_up(self, db: Session, monkeypatch):
        monkeypatch.setattr(settings, "agent_memory_ttl", 1)
        row = AgentMemory(
            session_id="expired",
            messages=[{"v": 1}],
            updated_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        db.add(row)
        db.flush()
        assert get_session_history(db, "expired") == []
        assert db.get(AgentMemory, "expired") is None


class TestSaveSessionHistory:
    def test_round_trip_cjk(self, db: Session):
        msgs = [{"role": "user", "content": "你好世界"}, {"role": "assistant", "content": "你好！"}]
        save_session_history(db, "cjk", msgs)
        assert get_session_history(db, "cjk") == msgs

    def test_nested_complex_messages(self, db: Session):
        msgs = [
            {
                "role": "assistant",
                "tool_calls": [
                    {"name": "parse_dxf", "args": {"layers": ["0", "A-WALL"], "precision": 0.001}}
                ],
                "content": None,
            }
        ]
        save_session_history(db, "nested", msgs)
        assert get_session_history(db, "nested") == msgs

    def test_truncates_to_max_messages(self, db: Session, monkeypatch):
        monkeypatch.setattr(settings, "agent_max_messages", 3)
        msgs = [{"n": i} for i in range(10)]
        save_session_history(db, "trunc", msgs)
        saved = get_session_history(db, "trunc")
        assert len(saved) == 3
        assert saved[-1] == {"n": 9}

    def test_exactly_at_max_messages(self, db: Session, monkeypatch):
        monkeypatch.setattr(settings, "agent_max_messages", 5)
        msgs = [{"n": i} for i in range(5)]
        save_session_history(db, "exact-limit", msgs)
        assert len(get_session_history(db, "exact-limit")) == 5

    def test_overwrite_replaces_previous(self, db: Session):
        save_session_history(db, "overwrite", [{"v": 1}])
        save_session_history(db, "overwrite", [{"v": 2}])
        assert get_session_history(db, "overwrite") == [{"v": 2}]

    def test_updated_at_refreshed_on_update(self, db: Session):
        save_session_history(db, "refresh", [{"x": 1}])
        row1 = db.get(AgentMemory, "refresh")
        assert row1 is not None
        ts1 = row1.updated_at
        save_session_history(db, "refresh", [{"x": 2}])
        db.refresh(row1)
        assert row1.updated_at >= ts1

    def test_multiple_sessions_independent(self, db: Session):
        save_session_history(db, "s-a", [{"id": "a"}])
        save_session_history(db, "s-b", [{"id": "b"}])
        assert get_session_history(db, "s-a") == [{"id": "a"}]
        assert get_session_history(db, "s-b") == [{"id": "b"}]


class TestDeleteSessionHistory:
    def test_removes_row(self, db: Session):
        save_session_history(db, "del", [{"a": 1}])
        delete_session_history(db, "del")
        assert get_session_history(db, "del") == []

    def test_idempotent_delete(self, db: Session):
        delete_session_history(db, "non-existent")  # must not raise


class TestAppendAndSave:
    def test_full_flow(self, db: Session):
        save_session_history(db, "flow", [{"seq": 1}])
        result = append_and_save(db, "flow", [{"seq": 2}, {"seq": 3}])
        assert len(result) == 3
        assert result == [{"seq": 1}, {"seq": 2}, {"seq": 3}]

    def test_truncation_on_append(self, db: Session, monkeypatch):
        monkeypatch.setattr(settings, "agent_max_messages", 2)
        save_session_history(db, "append-trunc", [{"n": 1}, {"n": 2}])
        result = append_and_save(db, "append-trunc", [{"n": 3}])
        assert result == [{"n": 2}, {"n": 3}]

    def test_empty_new_messages(self, db: Session):
        save_session_history(db, "empty-append", [{"n": 1}])
        result = append_and_save(db, "empty-append", [])
        assert result == [{"n": 1}]

    def test_no_existing_history(self, db: Session):
        result = append_and_save(db, "fresh", [{"n": 1}, {"n": 2}])
        assert result == [{"n": 1}, {"n": 2}]
        assert get_session_history(db, "fresh") == [{"n": 1}, {"n": 2}]
