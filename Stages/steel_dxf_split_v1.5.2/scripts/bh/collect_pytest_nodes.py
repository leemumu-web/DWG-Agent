#!/usr/bin/env python3
"""Emit exact pytest node ids as JSON for stable atomic-test collection."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

SENTINEL = "__PYTEST_NODE_IDS__="
ROOT = Path(__file__).resolve().parents[2]


class NodeCollector:
    def __init__(self) -> None:
        self.node_ids: list[str] = []

    def pytest_collection_finish(self, session) -> None:  # pytest hook
        self.node_ids = [item.nodeid for item in session.items]
        print(SENTINEL + json.dumps(self.node_ids, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output", type=Path)
    options, pytest_args = parser.parse_known_args(args)
    collector = NodeCollector()
    code = int(
        pytest.main(
            ["--collect-only", "-q", *pytest_args],
            plugins=[collector],
        )
    )
    if code == 0 and options.output is not None:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(
            json.dumps(collector.node_ids, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    main()
