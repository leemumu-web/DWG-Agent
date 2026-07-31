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
from app.modules.operations.control_plane.service import PIPELINE_QUEUE_MAP
from app.modules.workflows.templates import WORKFLOW_TEMPLATES


EXPECTED: dict[str, bool] = {
    "dxf_pipeline_enabled": True,
    "dxf2dwg_pipeline_enabled": True,
    "dxf2excel_pipeline_enabled": False,
    "dxf_classification_pipeline_enabled": True,
    "dxf_split_pipeline_enabled": True,
    "excel_final_pipeline_enabled": True,
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
    production_template = WORKFLOW_TEMPLATES["linux_production"]
    stage2 = next(
        (stage for stage in production_template.stages if stage.code == "excel_stage2"),
        None,
    )
    stage2_ready = bool(
        stage2 is not None
        and stage2.implementation_status == "implemented"
        and stage2.execution_kind == "excel_stage2"
        and PIPELINE_QUEUE_MAP.get("excel_stage2") == "excel_stage2"
    )
    if not stage2_ready:
        print("Excel 第二阶段常开能力的模板或队列路由不完整。", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "ok",
                "features": actual,
                "always_on_capabilities": {"excel_stage2": True},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
