from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.platform.config.settings import settings


class RemnantStageError(RuntimeError):
    """Client-safe parsing failure without subprocess output or local paths."""


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
    from remnant_drawing_reader.models import (
        ParseResult,
        ParseWarning,
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
        return ParseResult(
            schema_version=payload["schema_version"],
            parser_version=payload["parser_version"],
            source_sha256=payload["source_sha256"],
            material_candidates=_candidates(payload, "material_candidates"),
            project_candidates=_candidates(payload, "project_candidates"),
            part_candidates=_candidates(payload, "part_candidates"),
            warnings=[ParseWarning(**warning) for warning in payload["warnings"]],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RemnantStageError("REMNANT_PARSE_CONTRACT_INVALID") from exc
