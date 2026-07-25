from __future__ import annotations

import json
import os
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.box import delivery
from steel_dxf_split.box.compiler import (
    BoxCompileConfig,
    compile_box,
)
from steel_dxf_split.box.contracts import BoxSourceContract
from steel_dxf_split.box.release import write_box_release_attestation
from tests.box_v1.paths import INPUTS

SAMPLE = INPUTS / "2b1-cb-56_拆板前.dxf"


@pytest.fixture(autouse=True)
def _small_previews(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(delivery.box_preview, "PREVIEW_DPI", 40)
    monkeypatch.setattr(delivery.box_preview, "PREVIEW_CANVAS_PIXELS", (600, 420))
    monkeypatch.setattr(delivery.box_preview, "PREVIEW_MAX_PIXELS", 300_000)


def _release(path: Path) -> Path:
    write_box_release_attestation(
        path,
        pair_count=20,
        calibration_count=10,
        acceptance_count=10,
        manifest_fingerprint="a" * 64,
        gate_fingerprint="b" * 64,
    )
    return path


def test_packaged_release_promotes_native_output_to_production(
    tmp_path: Path,
) -> None:
    result = compile_box(
        SAMPLE,
        config=BoxCompileConfig(
            output_dir=tmp_path,
            source_contract=BoxSourceContract(),
        ),
    )

    assert result.production_path is not None
    assert result.production_path.is_file()
    assert result.review_path is None
    assert result.report["automation_route"] == "auto_accepted"
    assert result.report["single_file_disposition"] == "auto_accept"
    assert result.report["writer"] == "native_lwpolyline_circle"
    assert result.report["codegen_purpose"] == "production"
    assert result.report["legacy_solver_called"] is False
    assert result.report["ground_truth_used_for_decision"] is False
    assert result.report["outputs"]["source_copy"] is None
    assert all(
        Path(path).is_file()
        for path in result.report["outputs"]["previews"].values()
        if isinstance(path, str) and path.lower().endswith(".png")
    )
    assert result.report["release_attestation"]["passed"] is True
    assert Path(result.report["release_attestation"]["release_path"]).name == (
        "box_release_attestation.json"
    )
    document = ezdxf.readfile(result.production_path)
    assert not list(document.modelspace().query("REGION"))
    assert list(document.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']"))
    assert result.report["saved_dxf"]["ok"] is True
    assert json.loads(result.report_path.read_text(encoding="utf-8")) == result.report


def test_valid_release_promotes_native_output_to_production(tmp_path: Path) -> None:
    release_path = _release(tmp_path / "release.json")
    output = tmp_path / "output"

    result = compile_box(
        SAMPLE,
        config=BoxCompileConfig(
            output_dir=output,
            source_contract=BoxSourceContract(),
            release_attestation_path=release_path,
            require_auto_accept=True,
        ),
    )

    assert result.production_path is not None
    assert result.production_path.is_file()
    assert result.review_path is None
    assert result.report["automation_route"] == "auto_accepted"
    assert result.report["release_attestation"]["passed"] is True
    assert result.report["saved_dxf"]["checks"]["no_acis_region_payload"] is True


def test_invalid_packaged_release_leaves_zero_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"

    def reject_packaged_release(_path: Path | None = None):
        raise ValueError("BOX 内置 release attestation 缺失或无效")

    monkeypatch.setattr(
        "steel_dxf_split.box.release.load_verified_box_release_attestation",
        reject_packaged_release,
    )

    with pytest.raises(ValueError, match="内置 release attestation"):
        compile_box(
            SAMPLE,
            config=BoxCompileConfig(
                output_dir=output,
                source_contract=BoxSourceContract(),
                require_auto_accept=True,
            ),
        )

    assert not list(output.rglob("*"))


def test_saved_dxf_failure_leaves_zero_new_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"
    monkeypatch.setattr(
        delivery,
        "validate_saved_dxf",
        lambda *_args, **_kwargs: {
            "ok": False,
            "checks": {"writer_closure": False},
        },
    )

    with pytest.raises(ValueError, match="writer_closure"):
        compile_box(
            SAMPLE,
            config=BoxCompileConfig(
                output_dir=output,
                source_contract=BoxSourceContract(),
            ),
        )

    assert not list(output.rglob("*"))


def test_partial_promotion_failure_restores_existing_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    backups = stage / "backups"
    stage.mkdir()
    output.mkdir()
    staged_first = stage / "first.new"
    staged_second = stage / "second.new"
    final_first = output / "first.dxf"
    final_second = output / "second.json"
    staged_first.write_bytes(b"new-first")
    staged_second.write_bytes(b"new-second")
    final_first.write_bytes(b"old-first")
    final_second.write_bytes(b"old-second")
    real_replace = os.replace

    def fail_second_promotion(source: str | Path, destination: str | Path) -> None:
        if Path(source) == staged_second:
            raise OSError("injected second promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(delivery.os, "replace", fail_second_promotion)

    with pytest.raises(OSError, match="second promotion failure"):
        delivery._promote_staged_files(
            (
                (staged_first, final_first),
                (staged_second, final_second),
            ),
            backup_dir=backups,
        )

    assert final_first.read_bytes() == b"old-first"
    assert final_second.read_bytes() == b"old-second"
    assert not list(backups.glob("*.bak"))
