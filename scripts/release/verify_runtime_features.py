#!/usr/bin/env python3
"""Fail a production smoke test when approved runtime features drift.

The probe intentionally reads and reports only the public boolean feature
matrix.  It must never serialize Settings or the process environment because
those objects contain production secrets.
"""

from __future__ import annotations

import json
import sys

from app.platform.config.settings import settings


EXPECTED: dict[str, bool] = {
    "dxf_pipeline_enabled": True,
    "dxf2dwg_pipeline_enabled": True,
    "dxf2excel_pipeline_enabled": False,
    "dxf_classification_pipeline_enabled": True,
    "dxf_split_pipeline_enabled": True,
    "excel_final_pipeline_enabled": True,
    "excel_stage2_pipeline_enabled": True,
    "remnant_inventory_enabled": True,
}


def main() -> int:
    actual = {name: bool(getattr(settings, name)) for name in EXPECTED}
    drift = {
        name: {"expected": expected, "actual": actual[name]}
        for name, expected in EXPECTED.items()
        if actual[name] is not expected
    }
    if drift:
        print(
            "生产功能运行配置不符合发布标准："
            + json.dumps(drift, ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {"status": "ok", "features": actual},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
