from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter


@dataclass(frozen=True, slots=True)
class IsolatedProcessResult:
    returncode: int
    output: str
    duration_seconds: float
    timed_out: bool


def run_isolated_process(
    command: Sequence[str | os.PathLike[str]],
    timeout_seconds: float,
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> IsolatedProcessResult:
    """Run one worker in a new POSIX session and kill its tree on timeout."""

    if os.name != "posix":
        raise RuntimeError("box-dxf-split requires a Linux/POSIX runtime")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    started = perf_counter()
    process = subprocess.Popen(
        [os.fspath(item) for item in command],
        cwd=Path(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        output, _ = process.communicate(timeout=10)
        duration = perf_counter() - started
        return IsolatedProcessResult(
            124,
            (output or "") + f"\nTIMEOUT after {timeout_seconds:g} seconds\n",
            duration,
            True,
        )
    if process.returncode is None:
        raise RuntimeError("isolated worker ended without a return code")
    return IsolatedProcessResult(
        int(process.returncode),
        output or "",
        perf_counter() - started,
        False,
    )
