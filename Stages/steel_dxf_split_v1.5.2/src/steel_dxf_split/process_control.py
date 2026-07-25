from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import time


ACTIVE_SUPERVISION_QUANTUM_SECONDS = 1.0


class ProcessTreeTerminationError(RuntimeError):
    """Raised when a timed-out worker tree cannot be proven terminated."""


@dataclass(frozen=True)
class IsolatedProcessResult:
    returncode: int
    output: str
    duration_seconds: float
    timed_out: bool
    active_supervision_seconds: float | None = None
    unbudgeted_wall_seconds: float = 0.0


@dataclass(frozen=True)
class _CommunicationOutcome:
    output: str
    active_supervision_seconds: float
    unbudgeted_wall_seconds: float
    timed_out: bool


def process_group_options() -> dict[str, object]:
    """Return Linux/POSIX Popen options for an isolated worker session."""

    if os.name != "posix":
        raise RuntimeError("steel-dxf-split requires a Linux/POSIX runtime")
    return {"start_new_session": True}


def _terminate_posix_process_tree(process: subprocess.Popen[str]) -> None:
    try:
        process_group = os.getpgid(process.pid)
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        return


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    _terminate_posix_process_tree(process)


def _communicate_with_active_budget(
    process: subprocess.Popen[str],
    *,
    timeout_seconds: float,
    quantum_seconds: float = ACTIVE_SUPERVISION_QUANTUM_SECONDS,
    clock: Callable[[], float] = time.perf_counter,
) -> _CommunicationOutcome:
    """Drain a worker while excluding host pauses from its timeout budget.

    ``perf_counter`` includes system suspend. One long ``communicate(timeout=...)``
    would therefore classify a
    healthy worker as timed out immediately after resume.  Short, retry-safe
    communication intervals let the timeout represent supervised execution:
    each expired interval consumes only its requested quantum, while excess
    wall time remains explicit telemetry.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if quantum_seconds <= 0:
        raise ValueError("quantum_seconds must be positive")
    active_seconds = 0.0
    unbudgeted_seconds = 0.0
    while active_seconds < timeout_seconds:
        interval_budget = min(
            quantum_seconds,
            timeout_seconds - active_seconds,
        )
        interval_started = clock()
        try:
            output, _ = process.communicate(timeout=interval_budget)
        except subprocess.TimeoutExpired:
            interval_wall = max(0.0, clock() - interval_started)
            active_seconds += interval_budget
            unbudgeted_seconds += max(0.0, interval_wall - interval_budget)
            continue
        interval_wall = max(0.0, clock() - interval_started)
        supervised_interval = min(interval_wall, interval_budget)
        active_seconds += supervised_interval
        unbudgeted_seconds += max(0.0, interval_wall - supervised_interval)
        return _CommunicationOutcome(
            output=output or "",
            active_supervision_seconds=active_seconds,
            unbudgeted_wall_seconds=unbudgeted_seconds,
            timed_out=False,
        )
    return _CommunicationOutcome(
        output="",
        active_supervision_seconds=active_seconds,
        unbudgeted_wall_seconds=unbudgeted_seconds,
        timed_out=True,
    )


def run_isolated_process(
    command: Sequence[str | os.PathLike[str]],
    timeout_seconds: float,
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> IsolatedProcessResult:
    """Run one worker and fail closed if its timed-out tree cannot be removed."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    normalized_command = [os.fspath(item) for item in command]
    started = time.perf_counter()
    process = subprocess.Popen(
        normalized_command,
        cwd=Path(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **process_group_options(),
    )
    outcome = _communicate_with_active_budget(
        process,
        timeout_seconds=timeout_seconds,
    )
    if not outcome.timed_out:
        if process.returncode is None:
            raise RuntimeError("isolated worker ended without a return code")
        duration_seconds = time.perf_counter() - started
        return IsolatedProcessResult(
            returncode=int(process.returncode),
            output=outcome.output,
            duration_seconds=duration_seconds,
            timed_out=False,
            active_supervision_seconds=outcome.active_supervision_seconds,
            unbudgeted_wall_seconds=max(
                0.0,
                duration_seconds - outcome.active_supervision_seconds,
            ),
        )
    _terminate_process_tree(process)
    try:
        output, _ = process.communicate(timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise ProcessTreeTerminationError(
            f"terminated PID {process.pid} did not close its output pipes"
        ) from exc
    duration_seconds = time.perf_counter() - started
    output = (output or "") + (
        f"\nTIMEOUT after {timeout_seconds:g} active supervision seconds "
        f"(wall={duration_seconds:.6f}s; "
        f"unbudgeted={max(0.0, duration_seconds - outcome.active_supervision_seconds):.6f}s)\n"
    )
    return IsolatedProcessResult(
        returncode=124,
        output=output,
        duration_seconds=duration_seconds,
        timed_out=True,
        active_supervision_seconds=outcome.active_supervision_seconds,
        unbudgeted_wall_seconds=max(
            0.0,
            duration_seconds - outcome.active_supervision_seconds,
        ),
    )
