from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from . import parse_dxf


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read remnant metadata candidates from one DXF")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.write_text(
        json.dumps(parse_dxf(args.input).to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
