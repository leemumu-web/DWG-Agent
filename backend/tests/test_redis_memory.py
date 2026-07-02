from __future__ import annotations

import app.core.redis_client as redis_module
from app.services.redis_memory import (
    append_and_save,
    delete_session_history,
    get_session_history,
    save_session_history,
)


# ---------------------------------------------------------------------------
# get_session_history
# ---------------------------------------------------------------------------
class TestGetSessionHistory:
    def test_empty_for_unknown_session(self):
        assert get_session_history("no-such-session") == []

    def test_returns_stored_messages(self):
        msgs = [{"role": "user", "content": "hello"}]
        save_session_history("s1", msgs)
        assert get_session_history("s1") == msgs

    def test_empty_list_stored_and_retrieved(self):
        save_session_history("empty", [])
        assert get_session_history("empty") == []

    def test_corrupted_json_is_reset(self):
        """Non-JSON data in the key → logged warning + returns [] + key deleted."""
        r = redis_module.get_redis()
        assert r is not None
        r.set("agent:memory:corrupt", "{{{broken")
        result = get_session_history("corrupt")
        assert result == []
        assert r.get("agent:memory:corrupt") is None  # key was cleaned up


# ---------------------------------------------------------------------------
# save_session_history
# ---------------------------------------------------------------------------
class TestSaveSessionHistory:
    def test_round_trip_cjk(self):
        msgs = [{"role": "user", "content": "你好世界"}, {"role": "assistant", "content": "你好！"}]
        save_session_history("cjk", msgs)
        assert get_session_history("cjk") == msgs

    def test_nested_complex_messages(self):
        msgs = [
            {
                "role": "assistant",
                "tool_calls": [{"name": "parse_dxf", "args": {"layers": ["0", "A-WALL"], "precision": 0.001}}],
                "content": None,
            }
        ]
        save_session_history("nested", msgs)
        assert get_session_history("nested") == msgs

    def test_truncates_to_max_messages(self, monkeypatch):
        from app.core import config

        monkeypatch.setattr(config.settings, "redis_max_messages", 3)
        msgs = [{"n": i} for i in range(10)]
        save_session_history("trunc", msgs)
        saved = get_session_history("trunc")
        assert len(saved) == 3
        assert saved[-1] == {"n": 9}

    def test_exactly_at_max_messages(self, monkeypatch):
        from app.core import config

        monkeypatch.setattr(config.settings, "redis_max_messages", 5)
        msgs = [{"n": i} for i in range(5)]
        save_session_history("exact-limit", msgs)
        assert len(get_session_history("exact-limit")) == 5

    def test_overwrite_replaces_previous(self):
        save_session_history("overwrite", [{"v": 1}])
        save_session_history("overwrite", [{"v": 2}])
        assert get_session_history("overwrite") == [{"v": 2}]

    def test_ttl_is_set(self):
        save_session_history("ttl-test", [{"x": 1}])
        r = redis_module.get_redis()
        assert r is not None
        ttl = r.ttl("agent:memory:ttl-test")
        assert 7100 <= ttl <= 7200

    def test_ttl_refreshed_on_update(self):
        save_session_history("ttl-refresh", [{"x": 1}])
        r = redis_module.get_redis()
        assert r is not None
        ttl1 = r.ttl("agent:memory:ttl-refresh")
        # immediate overwrite
        save_session_history("ttl-refresh", [{"x": 2}])
        ttl2 = r.ttl("agent:memory:ttl-refresh")
        assert ttl1 is not None and ttl2 is not None
        assert ttl2 >= ttl1 - 1  # TTL should be fresh (near original max)

    def test_multiple_sessions_independent(self):
        save_session_history("s-a", [{"id": "a"}])
        save_session_history("s-b", [{"id": "b"}])
        assert get_session_history("s-a") == [{"id": "a"}]
        assert get_session_history("s-b") == [{"id": "b"}]


# ---------------------------------------------------------------------------
# delete_session_history
# ---------------------------------------------------------------------------
class TestDeleteSessionHistory:
    def test_removes_key(self):
        save_session_history("del", [{"a": 1}])
        delete_session_history("del")
        assert get_session_history("del") == []

    def test_idempotent_delete(self):
        delete_session_history("non-existent")  # must not raise


# ---------------------------------------------------------------------------
# append_and_save (full §11.5 flow)
# ---------------------------------------------------------------------------
class TestAppendAndSave:
    def test_full_flow(self):
        save_session_history("flow", [{"seq": 1}])
        result = append_and_save("flow", [{"seq": 2}, {"seq": 3}])
        assert len(result) == 3
        assert result == [{"seq": 1}, {"seq": 2}, {"seq": 3}]

    def test_truncation_on_append(self, monkeypatch):
        from app.core import config

        monkeypatch.setattr(config.settings, "redis_max_messages", 2)
        save_session_history("append-trunc", [{"n": 1}, {"n": 2}])
        result = append_and_save("append-trunc", [{"n": 3}])
        assert result == [{"n": 2}, {"n": 3}]

    def test_empty_new_messages(self):
        save_session_history("empty-append", [{"n": 1}])
        result = append_and_save("empty-append", [])
        assert result == [{"n": 1}]

    def test_no_existing_history(self):
        result = append_and_save("fresh", [{"n": 1}, {"n": 2}])
        assert result == [{"n": 1}, {"n": 2}]
        # Also persisted
        assert get_session_history("fresh") == [{"n": 1}, {"n": 2}]


# ---------------------------------------------------------------------------
# Redis unavailable — all calls safe
# ---------------------------------------------------------------------------
class TestRedisUnavailable:
    def test_all_safe_when_redis_down(self, monkeypatch):
        monkeypatch.setattr(redis_module, "_redis_client", None)
        monkeypatch.setattr(redis_module, "_redis_available", False)

        assert get_session_history("s") == []
        save_session_history("s", [{"x": 1}])  # should not raise
        delete_session_history("s")  # should not raise
        # When Redis is down, append_and_save still returns the new messages
        # (they are computed in-memory; just not persisted)
        assert append_and_save("s", [{"x": 1}]) == [{"x": 1}]

    def test_get_history_returns_empty_not_crashes(self, monkeypatch):
        monkeypatch.setattr(redis_module, "_redis_client", None)
        monkeypatch.setattr(redis_module, "_redis_available", False)
        assert get_session_history("any") == []
