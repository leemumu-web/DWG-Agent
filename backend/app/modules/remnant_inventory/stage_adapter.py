"""经子进程调用 remnant_drawing_reader Stage 的 seam 适配器。

稳定错误契约（程序化错误码；错误文本仅面向用户）：超时 →
``REMNANT_PARSE_TIMEOUT``，非零退出或缺少侧车文件 →
``REMNANT_PARSE_FAILED``，载荷畸形/版本不匹配 →
``REMNANT_PARSE_CONTRACT_INVALID``。Stage 在输入旁写 ``.result.json``
侧车文件；其 ``schema_version`` 必须与 ``remnant_drawing_reader.models``
保持兼容。
"""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.platform.config.settings import settings


class RemnantStageError(RuntimeError):
    """客户端安全的解析失败，不携带子进程输出或本地路径。"""


def _candidates(payload: dict, name: str):
    from remnant_drawing_reader.models import Candidate, Evidence

    return [
        Candidate(
            value=item["value"],
            evidence=[Evidence(**evidence) for evidence in item.get("evidence", [])],
        )
        for item in payload[name]
    ]


def parse_staged_dxf(path: Path):
    """用 remnant_drawing_reader Stage 解析一个暂存 DXF。

    在 ``remnant_parse_timeout_seconds`` 限时的子进程中运行
    ``python -m remnant_drawing_reader.cli`` 并读取 ``.result.json`` 侧车。
    按上面的稳定 REMNANT_* 错误码抛 ``RemnantStageError``；载荷不含标准
    余料时 ``standard_offcut`` 为 None。
    """
    from remnant_drawing_reader.models import (
        ParseResult,
        ParseWarning,
        StandardOffcut,
    )

    output = path.with_suffix(".result.json")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "remnant_drawing_reader.cli",
                str(path),
                "--output",
                str(output),
            ],
            check=False,
            capture_output=True,
            timeout=settings.remnant_parse_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemnantStageError("REMNANT_PARSE_TIMEOUT") from exc
    if completed.returncode != 0 or not output.is_file():
        raise RemnantStageError("REMNANT_PARSE_FAILED")
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
        standard_payload = payload.get("standard_offcut")
        standard_offcut = (
            StandardOffcut(
                block_type=standard_payload["block_type"],
                raw_specification=standard_payload["raw_specification"],
                thickness=Decimal(str(standard_payload["thickness"])),
                length=Decimal(str(standard_payload["length"])),
                width=Decimal(str(standard_payload["width"])),
                material=standard_payload["material"],
                remnant_number=standard_payload["remnant_number"],
            )
            if standard_payload is not None
            else None
        )
        return ParseResult(
            schema_version=payload["schema_version"],
            parser_version=payload["parser_version"],
            source_sha256=payload["source_sha256"],
            material_candidates=_candidates(payload, "material_candidates"),
            project_candidates=_candidates(payload, "project_candidates"),
            part_candidates=_candidates(payload, "part_candidates"),
            warnings=[ParseWarning(**warning) for warning in payload["warnings"]],
            standard_offcut=standard_offcut,
        )
    except (KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError) as exc:
        raise RemnantStageError("REMNANT_PARSE_CONTRACT_INVALID") from exc
