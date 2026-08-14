"""Seam adapter invoking the remnant_drawing_reader Stage via subprocess.

Stable error contract (programmatic codes; the message text is user-facing
only): timeout → ``REMNANT_PARSE_TIMEOUT``, non-zero exit or missing sidecar
→ ``REMNANT_PARSE_FAILED``, malformed/version-mismatched payload →
``REMNANT_PARSE_CONTRACT_INVALID``. The Stage writes a ``.result.json``
sidecar next to the input; its ``schema_version`` must stay compatible with
``remnant_drawing_reader.models``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal, InvalidOperation
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
    """Parse one staged DXF with the remnant_drawing_reader Stage.

    Runs ``python -m remnant_drawing_reader.cli`` in a subprocess bounded by
    ``remnant_parse_timeout_seconds`` and reads the ``.result.json`` sidecar.
    Raises ``RemnantStageError`` with the stable REMNANT_* codes above;
    ``standard_offcut`` is None when the payload carries no standard offcut.
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
