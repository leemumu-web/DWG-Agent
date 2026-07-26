from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.cad_processing.dwg_to_dxf.progress import (
    CLAIMED,
    COMPLETED,
    ODA_CONVERTING,
    ODA_RESULT_READY,
    PERSISTING,
    phase_event,
    safe_convert_result_metadata,
)


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        (CLAIMED, (5, "claimed", False)),
        (ODA_CONVERTING, (20, "oda_converting", True)),
        (ODA_RESULT_READY, (70, "oda_result_ready", False)),
        (PERSISTING, (85, "persisting", False)),
        (COMPLETED, (100, "completed", False)),
    ],
)
def test_dwg_progress_uses_confirmed_milestones(phase, expected):
    event = phase_event(phase)

    assert (event["progress"], event["phase"], event["indeterminate"]) == expected
    assert event["progress_basis"] == "confirmed_milestone"
    assert event["phase_label"]
    assert event["message"]


def test_safe_convert_result_metadata_excludes_paths_and_subprocess_details():
    result = SimpleNamespace(
        source=Path("/tmp/private/source.dwg"),
        target=Path("/tmp/private/result.dxf"),
        success=True,
        returncode=0,
        duration=1.23456,
        stdout="secret",
        stderr="secret",
    )

    assert safe_convert_result_metadata(result) == {
        "success": True,
        "duration_seconds": 1.235,
    }
