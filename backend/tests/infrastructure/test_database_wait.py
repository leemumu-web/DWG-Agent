from __future__ import annotations

from contextlib import nullcontext

from app.platform.database import wait


class _Connection:
    def execute(self, statement) -> None:
        assert str(statement) == "SELECT 1"


class _Engine:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.attempts = 0

    def connect(self):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise ConnectionRefusedError("database secret must not reach logs")
        return nullcontext(_Connection())


def test_database_wait_retries_until_the_listener_is_reachable(monkeypatch, capsys):
    engine = _Engine(failures=2)
    sleeps: list[float] = []
    monkeypatch.setattr(wait, "engine", engine)

    assert wait.wait_for_database(
        max_attempts=3,
        delay_seconds=0.25,
        sleep=sleeps.append,
    )

    assert engine.attempts == 3
    assert sleeps == [0.25, 0.25]
    output = capsys.readouterr()
    assert "database secret" not in output.out
    assert "database secret" not in output.err
    assert "ConnectionRefusedError" in output.err
    assert "Database connection is ready." in output.out


def test_database_wait_fails_after_a_bounded_number_of_attempts(monkeypatch, capsys):
    engine = _Engine(failures=3)
    sleeps: list[float] = []
    monkeypatch.setattr(wait, "engine", engine)

    assert not wait.wait_for_database(
        max_attempts=3,
        delay_seconds=0.5,
        sleep=sleeps.append,
    )

    assert engine.attempts == 3
    assert sleeps == [0.5, 0.5]
    assert "Database connection did not become ready after 3 attempts." in capsys.readouterr().err
