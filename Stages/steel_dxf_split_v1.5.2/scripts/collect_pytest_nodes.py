#!/usr/bin/env python3
"""Emit exact pytest node ids as JSON for stable atomic-test collection."""
from __future__ import annotations

import json
import os
import sys

import pytest

SENTINEL = "__PYTEST_NODE_IDS__="


class NodeCollector:
    def pytest_collection_finish(self, session) -> None:  # pytest hook
        node_ids = [item.nodeid for item in session.items]
        print(SENTINEL + json.dumps(node_ids, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    code = int(pytest.main(["--collect-only", "-q", *args], plugins=[NodeCollector()]))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    main()
