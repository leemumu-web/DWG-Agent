#!/usr/bin/env python3
"""Execute one pytest node with deterministic process termination.

Large ezdxf/Shapely regression cases can finish all assertions but stall while
pytest tears down the session or Python finalizes native-backed cyclic objects.
Waiting for ``pytest.main()`` to return is therefore too late on affected
runners.  This worker exits from ``pytest_sessionfinish``: at that point pytest
has computed the authoritative exit status and completed test reporting, while
interpreter/global finalizers have not started.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


class _ExitAtSessionFinish:
    """Terminate immediately after pytest has determined the session status."""

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        del session
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(int(exitstatus))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: pytest_worker.py <pytest-node-id>", file=sys.stderr)
        return 2
    node = sys.argv[1]
    # Preserve parametrized node ids; resolving them as filesystem paths would
    # corrupt strings such as ``test_name[param]``.
    if "::" not in node:
        node = str(Path(node).resolve())
    return int(pytest.main(["-q", node], plugins=[_ExitAtSessionFinish()]))


if __name__ == "__main__":
    code = main()
    # Collection/setup failures can occur before pytest_sessionfinish is called.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
