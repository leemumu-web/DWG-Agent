from __future__ import annotations

import pytest
from fakeredis import FakeRedis

import app.core.redis_client as redis_module


@pytest.fixture(autouse=True)
def _isolate_redis_client(monkeypatch):
    """Replace the real Redis module-level singleton with a FakeRedis for every test.

    This ensures tests never touch a real Redis server and provide full isolation
    between test cases (keys are flushed on teardown).
    """
    fake = FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_module, "_redis_client", fake)
    monkeypatch.setattr(redis_module, "_redis_available", True)
    yield
    fake.flushall()
    fake.close()
