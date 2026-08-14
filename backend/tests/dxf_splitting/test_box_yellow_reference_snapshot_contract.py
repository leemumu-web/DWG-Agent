from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
from tools.box_acceptance.reference_snapshot import (
    CircleSnapshot,
    PolylineSnapshot,
    TextSnapshot,
    load_reference_snapshot,
)

from tests.support.paths import REPO_ROOT

REPOSITORY_ROOT = REPO_ROOT
EXTRACT_SCRIPT = (
    REPOSITORY_ROOT / "scripts/cad/extract_box_yellow_reference_snapshots.ps1"
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _manifest(
    path: Path,
    *,
    corpus_root: Path,
    sample_id: str = "a1-cb-1",
    relative_path: str = "answers/correct.dwg",
    digest: str,
) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "BOX-EXTERNAL-ACCEPTANCE-CONTRACTS-1.0",
                "corpus_root": str(corpus_root.resolve()),
                "sample_count": 1,
                "samples": [
                    {
                        "sample_id": sample_id,
                        "evidence_level": "complete_reference",
                        "complete_reference": {
                            "relative_path": relative_path,
                            "sha256": digest,
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _run(
    manifest: Path,
    output_root: Path,
    *,
    sample_id: str | None = None,
    evidence_field: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(EXTRACT_SCRIPT),
        "-ManifestPath",
        str(manifest),
        "-OutputRoot",
        str(output_root),
    ]
    if sample_id is not None:
        command.extend(("-SampleId", sample_id))
    if evidence_field is not None:
        command.extend(("-EvidenceField", evidence_field))
    return subprocess.run(command, capture_output=True, check=False)


def _combined_output(result: subprocess.CompletedProcess[bytes]) -> str:
    return (result.stdout + result.stderr).decode("utf-8", errors="replace")


def _assert_extract_error(
    result: subprocess.CompletedProcess[bytes],
    code: str,
) -> None:
    output = _combined_output(result)
    assert result.returncode != 0
    assert f"{code}:" in output
    assert "Error formatting a string" not in output


def _snapshot_payload(source_sha256: str) -> dict[str, object]:
    return {
        "schema": "BOX-YELLOW-REFERENCE-SNAPSHOT-1.0",
        "sample_id": "a1-cb-8",
        "source": {
            "relative_path": "answers/correct.dwg",
            "sha256_before": source_sha256,
            "sha256_after": source_sha256,
            "unchanged": True,
        },
        "zwcad_progid": "ZWCAD.Application.2026",
        "model_space_count": 3,
        "entities": [
            {
                "object_name": "AcDbPolyline",
                "handle": "10",
                "layer": "PLATE_CUT",
                "color": 2,
                "coordinates": [0.0, 0.0, 10.0, 0.0, 10.0, 4.0, 0.0, 4.0],
                "bulges": [0.0, 0.5, 0.0, 0.0],
                "closed": True,
                "elevation": 0.0,
                "normal": [0.0, 0.0, 1.0],
            },
            {
                "object_name": "AcDbCircle",
                "handle": "11",
                "layer": "CUT_HOLE",
                "color": 2,
                "center": [5.0, 2.0, 0.0],
                "radius": 1.0,
                "normal": [0.0, 0.0, 1.0],
            },
            {
                "object_name": "AcDbText",
                "handle": "12",
                "layer": "PART_LABEL",
                "color": 2,
                "text": "p=a1-cb-8上翼",
                "insertion_point": [1.0, 1.0, 0.0],
                "height": 10.0,
                "rotation": 0.0,
            },
        ],
    }


def test_snapshot_loader_preserves_every_supported_geometry_field(
    tmp_path: Path,
) -> None:
    digest = "a" * 64
    path = tmp_path / "a1-cb-8_correct-reference.json"
    path.write_text(
        json.dumps(_snapshot_payload(digest), ensure_ascii=False),
        encoding="utf-8",
    )

    snapshot = load_reference_snapshot(path, expected_source_sha256=digest)

    assert snapshot.sample_id == "a1-cb-8"
    assert snapshot.source_unchanged is True
    assert len(snapshot.entities) == 3
    polyline = snapshot.entities[0]
    circle = snapshot.entities[1]
    text = snapshot.entities[2]
    assert isinstance(polyline, PolylineSnapshot)
    assert polyline.bulges == (0.0, 0.5, 0.0, 0.0)
    assert isinstance(circle, CircleSnapshot)
    assert circle.radius == 1.0
    assert isinstance(text, TextSnapshot)
    assert text.text == "p=a1-cb-8上翼"


def test_snapshot_keeps_foreign_member_label_as_auditable_evidence(
    tmp_path: Path,
) -> None:
    digest = "e" * 64
    payload = _snapshot_payload(digest)
    payload["sample_id"] = "a1-cb-4"
    payload["entities"][2]["text"] = "p=a1-cb-3下翼"
    path = tmp_path / "a1-cb-4_correct-reference.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    snapshot = load_reference_snapshot(path, expected_source_sha256=digest)

    text = snapshot.entities[2]
    assert isinstance(text, TextSnapshot)
    assert text.text == "p=a1-cb-3下翼"
    assert snapshot.foreign_member_labels == ("p=a1-cb-3下翼",)


def test_snapshot_loader_fails_closed_for_unknown_entity_type(tmp_path: Path) -> None:
    digest = "b" * 64
    payload = _snapshot_payload(digest)
    payload["entities"].append(
        {
            "object_name": "AcDbSpline",
            "handle": "13",
            "layer": "PLATE_CUT",
            "color": 2,
        }
    )
    payload["model_space_count"] = 4
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported reference entity"):
        load_reference_snapshot(path, expected_source_sha256=digest)


@pytest.mark.parametrize(
    ("before", "after", "unchanged"),
    (("c" * 64, "d" * 64, False), ("c" * 64, "c" * 64, False)),
)
def test_snapshot_loader_rejects_any_source_drift_claim(
    tmp_path: Path,
    before: str,
    after: str,
    unchanged: bool,
) -> None:
    payload = _snapshot_payload(before)
    payload["source"]["sha256_after"] = after
    payload["source"]["unchanged"] = unchanged
    path = tmp_path / "drift.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="source hash proof"):
        load_reference_snapshot(path, expected_source_sha256=before)


def test_extractor_is_read_only_and_has_no_dwg_write_route() -> None:
    source = EXTRACT_SCRIPT.read_text(encoding="utf-8")

    assert "$documents.Open($item.SourcePath, $true)" in source
    assert ".SaveAs(" not in source
    assert ".Export(" not in source
    assert "Copy-Item" not in source
    assert '"AcDbPolyline"' in source
    assert '"AcDbCircle"' in source
    assert '"AcDbText"' in source
    assert "UNSUPPORTED_REFERENCE_ENTITY" in source


def test_extractor_rejects_output_directory_inside_read_only_corpus(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    source = corpus / "answers/correct.dwg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"AC1032-correct")
    manifest = _manifest(
        tmp_path / "manifest.json",
        corpus_root=corpus,
        digest=_digest(source),
    )

    result = _run(manifest, corpus / "generated")

    _assert_extract_error(result, "OUTPUT_ROOT_INSIDE_CORPUS")
    assert source.read_bytes() == b"AC1032-correct"


def test_extractor_stops_before_cad_when_source_hash_does_not_match(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    source = corpus / "answers/correct.dwg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"AC1032-correct")
    manifest = _manifest(
        tmp_path / "manifest.json",
        corpus_root=corpus,
        digest="0" * 64,
    )

    result = _run(manifest, tmp_path / "snapshots")

    _assert_extract_error(result, "SOURCE_SHA256_MISMATCH")
    assert not (tmp_path / "snapshots").exists()


def test_extractor_selects_historical_wrong_result_evidence_before_cad(
    tmp_path: Path,
) -> None:
    """Catch historical mode silently reopening the yellow-answer field."""

    corpus = tmp_path / "corpus"
    source = corpus / "results/old-wrong.dwg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"AC1032-old-wrong")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "BOX-EXTERNAL-ACCEPTANCE-CONTRACTS-1.0",
                "corpus_root": str(corpus.resolve()),
                "sample_count": 1,
                "samples": [
                    {
                        "sample_id": "b4-3-cb-19",
                        "evidence_level": "human_constraint",
                        "complete_reference": None,
                        "historical_wrong_result": {
                            "relative_path": "results/old-wrong.dwg",
                            "sha256": "0" * 64,
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = _run(
        manifest,
        tmp_path / "snapshots",
        evidence_field="historical_wrong_result",
    )

    _assert_extract_error(result, "SOURCE_SHA256_MISMATCH")
    assert source.read_bytes() == b"AC1032-old-wrong"


def test_extractor_rejects_reference_path_outside_corpus(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    outside = tmp_path / "outside.dwg"
    outside.write_bytes(b"AC1032-outside")
    manifest = _manifest(
        tmp_path / "manifest.json",
        corpus_root=corpus,
        relative_path="../outside.dwg",
        digest=_digest(outside),
    )

    result = _run(manifest, tmp_path / "snapshots")

    _assert_extract_error(result, "SOURCE_PATH_OUTSIDE_CORPUS")
    assert outside.read_bytes() == b"AC1032-outside"


def test_extractor_rejects_unknown_requested_sample_before_cad(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    source = corpus / "answers/correct.dwg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"AC1032-correct")
    manifest = _manifest(
        tmp_path / "manifest.json",
        corpus_root=corpus,
        digest=_digest(source),
    )

    result = _run(
        manifest,
        tmp_path / "snapshots",
        sample_id="missing-sample",
    )

    _assert_extract_error(result, "SAMPLE_NOT_FOUND")
    assert not (tmp_path / "snapshots").exists()
