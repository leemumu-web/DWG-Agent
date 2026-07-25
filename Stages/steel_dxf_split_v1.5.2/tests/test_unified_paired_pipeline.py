from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import steel_dxf_split.pipeline as pipeline
from steel_dxf_split import weld_allowance as bh_weld_allowance
from steel_dxf_split.bh_knowledge import BHSourceContract
from steel_dxf_split.box import weld_allowance as box_weld_allowance
from steel_dxf_split.box.contracts import BoxSourceContract
from steel_dxf_split.pipeline import SplitOptions, split_dxf
from steel_dxf_split.weld_allowance import WeldAllowanceProcessingError


def _native_report(family: str) -> dict[str, object]:
    if family == "BH":
        return {
            "version": "1.5.2",
            "report_schema": "BH-COMPILATION-REPORT-1.4",
            "profile_family": "BH",
            "automation_route": "production",
            "automation_assessment": {"disposition": "auto_accept"},
            "proof_report": {"disposition": "auto_accept", "obligations": []},
            "outputs": {
                "production_clean": None,
                "review_candidate": None,
                "source_copy": None,
                "previews": None,
            },
            "search_status": {"search_complete": True},
            "semantic_fingerprints": {"manufacturing_ir": "b" * 64},
            "compiler": {"stages": []},
            "preview_rendering": {"render_seconds": 0.0},
            "validation": {"values": {"mass_error_pct": 0.0}},
            "diagnostic_codes": [],
            "saved_dxf": {"ok": True},
        }
    return {
        "version": "1.0.0",
        "report_schema": "BOX-COMPILATION-REPORT-4.0",
        "profile_family": "BOX",
        "automation_route": "auto_accepted",
        "single_file_disposition": "auto_accept",
        "proof_report": {"disposition": "auto_accept", "obligations": []},
        "outputs": {
            "production_clean": None,
            "review_candidate": None,
            "source_copy": None,
            "previews": None,
        },
        "search_status": {"search_complete": True},
        "manufacturing_ir": {"fingerprint": "c" * 64},
        "timing": {"preview_render_seconds": 0.0},
        "saved_dxf": {"ok": True},
    }


def _install_native_success(
    family: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: dict[str, int],
) -> SplitOptions:
    input_path = tmp_path / "member_拆板前.dxf"
    input_path.write_bytes(b"source")
    monkeypatch.setattr(pipeline, "load_document", lambda _path: object())

    def detect(_document: object) -> str:
        calls["detect"] += 1
        return family

    monkeypatch.setattr(pipeline, "detect_profile_family", detect)

    if family == "BH":
        def fake_split_bh(
            _input_path: Path,
            output_dir: Path,
            **_kwargs: object,
        ) -> tuple[Path, None, Path, dict[str, object]]:
            calls["split"] += 1
            native_dir = Path(output_dir) / "auto_accepted"
            native_dir.mkdir(parents=True)
            normal = native_dir / "member_自动拆板_清洁1to1.dxf"
            report_path = native_dir / "member_自动拆板_报告.json"
            normal.write_bytes(b"normal")
            report = _native_report("BH")
            report["outputs"]["production_clean"] = str(normal.resolve())
            report_path.write_text(json.dumps(report), encoding="utf-8")
            return normal, None, report_path, report

        monkeypatch.setattr(pipeline, "split_bh_dxf", fake_split_bh)
        return SplitOptions(source_contract=BHSourceContract())

    from steel_dxf_split.box import compiler

    def fake_compile_box(_input_path: Path, *, config: object) -> SimpleNamespace:
        calls["split"] += 1
        native_dir = Path(config.output_dir) / "auto_accepted"
        native_dir.mkdir(parents=True)
        normal = native_dir / "member_自动拆板_清洁1to1.dxf"
        report_path = native_dir / "member_自动拆板_报告.json"
        normal.write_bytes(b"normal")
        report = _native_report("BOX")
        report["outputs"]["production_clean"] = str(normal.resolve())
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return SimpleNamespace(
            production_path=normal,
            review_path=None,
            report_path=report_path,
            report=report,
        )

    monkeypatch.setattr(compiler, "compile_box", fake_compile_box)
    return SplitOptions(box_source_contract=BoxSourceContract())


@pytest.mark.parametrize("family", ["BH", "BOX"])
def test_one_detection_and_split_publish_exactly_one_complete_dxf_pair(
    family: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"detect": 0, "split": 0, "allowance": 0}
    options = _install_native_success(family, tmp_path, monkeypatch, calls)

    def fake_allowance(
        input_path: Path,
        compilation_report_path: Path,
        output_path: Path,
        report_path: Path,
    ) -> SimpleNamespace:
        calls["allowance"] += 1
        assert input_path.read_bytes() == b"normal"
        assert compilation_report_path.is_file()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"normal-with-allowance")
        report_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "input_split_dxf": str(input_path.resolve()),
                    "input_compilation_report": str(
                        compilation_report_path.resolve()
                    ),
                    "output_dxf": str(output_path.resolve()),
                    "original_split_result_preserved": True,
                    "checks": {"verified": True},
                    "plates" if family == "BH" else "groups": [],
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(output_path=output_path, report_path=report_path)

    target = bh_weld_allowance if family == "BH" else box_weld_allowance
    monkeypatch.setattr(target, "apply_weld_allowance", fake_allowance)
    monkeypatch.setattr(
        pipeline,
        "validate_paired_outputs",
        lambda *_args, **_kwargs: {"ok": True, "checks": {"paired": True}},
    )
    output = tmp_path / "output"
    previous_manual_task = output / "manual_review" / family.lower() / "member"
    previous_manual_task.mkdir(parents=True)
    (previous_manual_task / "member_review_candidate.dxf").write_bytes(
        b"old-review"
    )

    result = split_dxf(tmp_path / "member_拆板前.dxf", output, options)

    task_dir = output / "auto_accepted" / family.lower() / "member"
    normal = task_dir / "member_正常拆板.dxf"
    allowance = task_dir / "member_余量增长.dxf"
    assert calls == {"detect": 1, "split": 1, "allowance": 1}
    assert result.production_path == normal
    assert result.weld_allowance_path == allowance
    assert normal.read_bytes() == b"normal"
    assert allowance.read_bytes() == b"normal-with-allowance"
    assert sorted(path.name for path in task_dir.glob("*.dxf")) == [
        "member_余量增长.dxf",
        "member_正常拆板.dxf",
    ]
    assert len(tuple(output.rglob("*.dxf"))) == 2
    assert not previous_manual_task.exists()
    assert not tuple(output.glob(".steel-dxf-task-*"))


def test_allowance_failure_routes_the_whole_task_to_manual_review_without_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"detect": 0, "split": 0, "allowance": 0}
    options = _install_native_success("BH", tmp_path, monkeypatch, calls)

    def fail_allowance(*_args: object, **_kwargs: object) -> None:
        calls["allowance"] += 1
        raise WeldAllowanceProcessingError("injected allowance proof failure")

    monkeypatch.setattr(bh_weld_allowance, "apply_weld_allowance", fail_allowance)
    output = tmp_path / "output"
    previous_auto_task = output / "auto_accepted" / "bh" / "member"
    previous_auto_task.mkdir(parents=True)
    (previous_auto_task / "member_正常拆板.dxf").write_bytes(b"old-normal")
    (previous_auto_task / "member_余量增长.dxf").write_bytes(
        b"old-allowance"
    )

    result = split_dxf(tmp_path / "member_拆板前.dxf", output, options)

    manual_task = output / "manual_review" / "bh" / "member"
    assert result.automation_route == "manual_review"
    assert result.production_path is None
    assert result.weld_allowance_path is None
    assert result.review_candidate_path == manual_task / "member_normal_candidate.dxf"
    assert result.review_candidate_path.is_file()
    assert not previous_auto_task.exists()
    assert not tuple((output / "auto_accepted").rglob("*.dxf"))
    assert calls == {"detect": 1, "split": 1, "allowance": 1}


def test_task_directory_promotion_restores_previous_pair_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = tmp_path / "staged"
    final = tmp_path / "output" / "auto_accepted" / "bh" / "member"
    staged.mkdir(parents=True)
    final.mkdir(parents=True)
    (staged / "member_正常拆板.dxf").write_bytes(b"new-normal")
    (staged / "member_余量增长.dxf").write_bytes(b"new-allowance")
    (final / "member_正常拆板.dxf").write_bytes(b"old-normal")
    (final / "member_余量增长.dxf").write_bytes(b"old-allowance")
    real_replace = os.replace
    failed = False

    def fail_new_directory_once(source: str | Path, destination: str | Path) -> None:
        nonlocal failed
        if Path(source) == staged and Path(destination) == final and not failed:
            failed = True
            raise OSError("injected task promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(pipeline.os, "replace", fail_new_directory_once)

    with pytest.raises(OSError, match="task promotion failure"):
        pipeline._promote_task_directory(staged, final)

    assert (final / "member_正常拆板.dxf").read_bytes() == b"old-normal"
    assert (final / "member_余量增长.dxf").read_bytes() == b"old-allowance"
    assert staged.is_dir()
    assert not tuple(final.parent.glob(".*paired-output-backup*"))
