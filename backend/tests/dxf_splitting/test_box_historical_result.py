from __future__ import annotations

import json
from pathlib import Path

from tools.box_acceptance.historical_result import load_historical_result


def test_historical_result_reads_labels_contours_and_both_opening_kinds(
    tmp_path: Path,
) -> None:
    """Catch a parser that drops an old result's role, contour, or openings."""

    digest = "a" * 64
    snapshot = tmp_path / "b4-3-cb-19_historical-wrong-result.json"
    snapshot.write_text(
        json.dumps(
            {
                "schema": "BOX-HISTORICAL-WRONG-RESULT-SNAPSHOT-1.0",
                "sample_id": "b4-3-cb-19",
                "source": {
                    "relative_path": "02_程序错误结果_b4-3-cb-19.dwg",
                    "sha256_before": digest,
                    "sha256_after": digest,
                    "unchanged": True,
                },
                "zwcad_progid": "ZWCAD.Application.2026",
                "model_space_count": 6,
                "entities": [
                    {
                        "object_name": "AcDbPolyline",
                        "handle": "plate-web",
                        "layer": "PLATE_CUT",
                        "color": 256,
                        "coordinates": [0, 0, 200, 0, 200, 80, 0, 80],
                        "bulges": [0, 0, 0, 0],
                        "closed": True,
                        "elevation": 0,
                        "normal": [0, 0, 1],
                    },
                    {
                        "object_name": "AcDbPolyline",
                        "handle": "plate-flange",
                        "layer": "PLATE_CUT",
                        "color": 256,
                        "coordinates": [300, 0, 480, 0, 480, 60, 300, 60],
                        "bulges": [0, 0, 0, 0],
                        "closed": True,
                        "elevation": 0,
                        "normal": [0, 0, 1],
                    },
                    {
                        "object_name": "AcDbCircle",
                        "handle": "circle-hole",
                        "layer": "CUT_HOLE",
                        "color": 256,
                        "center": [50, 40, 0],
                        "radius": 10,
                        "normal": [0, 0, 1],
                    },
                    {
                        "object_name": "AcDbPolyline",
                        "handle": "slot-hole",
                        "layer": "CUT_HOLE",
                        "color": 256,
                        "coordinates": [120, 30, 150, 30, 150, 50, 120, 50],
                        "bulges": [0, 0, 0, 0],
                        "closed": True,
                        "elevation": 0,
                        "normal": [0, 0, 1],
                    },
                    {
                        "object_name": "AcDbText",
                        "handle": "label-web",
                        "layer": "PART_LABEL",
                        "color": 256,
                        "text": "p=b4-3-cb-19上腹",
                        "insertion_point": [100, 40, 0],
                        "height": 20,
                        "rotation": 0,
                    },
                    {
                        "object_name": "AcDbText",
                        "handle": "label-flange",
                        "layer": "PART_LABEL",
                        "color": 256,
                        "text": "p=b4-3-cb-19翼",
                        "insertion_point": [390, 30, 0],
                        "height": 20,
                        "rotation": 0,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = load_historical_result(
        snapshot,
        expected_source_sha256=digest,
        expected_member_mark="b4-3-cb-19",
    )

    assert result.sample_id == "b4-3-cb-19"
    assert result.source_relative_path == "02_程序错误结果_b4-3-cb-19.dwg"
    assert [(plate.label, plate.family, plate.quantity) for plate in result.plates] == [
        ("上腹", "web", 1),
        ("翼", "flange", 2),
    ]
    assert result.plates[0].shape.polygon.bounds == (0.0, 0.0, 200.0, 80.0)
    assert [(opening.kind, opening.radius) for opening in result.plates[0].openings] == [
        ("POLYGON", None),
        ("CIRCLE", 10.0),
    ]
