from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from steel_dxf_split import process_control
from steel_dxf_split.process_control import (
    process_group_options,
    run_isolated_process,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "bh_v152" / "process_tree_fixture.py"


def _listener_reachable(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups are Linux-only")
def test_processes_start_a_new_posix_session() -> None:
    assert process_group_options() == {"start_new_session": True}


def test_non_posix_runtime_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_control.os, "name", "nt")
    with pytest.raises(RuntimeError, match="Linux/POSIX"):
        process_group_options()


def test_active_supervision_budget_excludes_a_long_host_pause() -> None:
    class ControlledClock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += seconds

    class PausedProcess:
        returncode: int | None = None
        calls = 0

        def communicate(self, timeout: float):
            self.calls += 1
            if self.calls == 1:
                assert timeout == 1.0
                clock.advance(1_800.0)
                raise subprocess.TimeoutExpired(["worker"], timeout)
            assert timeout == 1.0
            clock.advance(0.25)
            self.returncode = 0
            return "complete", None

    clock = ControlledClock()
    process = PausedProcess()

    outcome = process_control._communicate_with_active_budget(
        process,
        timeout_seconds=2.0,
        quantum_seconds=1.0,
        clock=clock,
    )

    assert process.calls == 2
    assert outcome.timed_out is False
    assert outcome.output == "complete"
    assert outcome.active_supervision_seconds == 1.25
    assert outcome.unbudgeted_wall_seconds == 1_799.0


def test_active_supervision_budget_still_terminates_a_continuously_hung_worker() -> None:
    class ControlledClock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += seconds

    class HungProcess:
        returncode = None
        calls = 0

        def communicate(self, timeout: float):
            self.calls += 1
            clock.advance(timeout)
            raise subprocess.TimeoutExpired(["worker"], timeout)

    clock = ControlledClock()
    process = HungProcess()

    outcome = process_control._communicate_with_active_budget(
        process,
        timeout_seconds=2.0,
        quantum_seconds=1.0,
        clock=clock,
    )

    assert process.calls == 2
    assert outcome.timed_out is True
    assert outcome.active_supervision_seconds == 2.0
    assert outcome.unbudgeted_wall_seconds == 0.0


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups are Linux-only")
def test_timeout_terminates_the_complete_process_tree(tmp_path: Path) -> None:
    port_path = tmp_path / "listener-port.txt"

    result = run_isolated_process(
        [sys.executable, str(FIXTURE), str(port_path)],
        timeout_seconds=1.0,
    )

    assert result.returncode == 124
    assert result.timed_out is True
    assert "listener-child-pid=" in result.output
    assert "TIMEOUT after 1" in result.output
    assert result.active_supervision_seconds == 1.0
    assert result.unbudgeted_wall_seconds >= 0.0
    assert result.duration_seconds >= result.active_supervision_seconds
    assert port_path.is_file()
    port = int(port_path.read_text(encoding="ascii"))
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and _listener_reachable(port):
        time.sleep(0.05)
    assert not _listener_reachable(port)


def test_worker_callers_do_not_own_platform_termination_policy() -> None:
    callers = (
        ROOT / "src" / "steel_dxf_split" / "layered_cli.py",
        ROOT / "scripts" / "bh" / "run_atomic_test_matrix.py",
    )
    for path in callers:
        source = path.read_text(encoding="utf-8")
        assert "os.killpg" not in source
        assert "signal.SIGKILL" not in source
        assert '"taskkill"' not in source
