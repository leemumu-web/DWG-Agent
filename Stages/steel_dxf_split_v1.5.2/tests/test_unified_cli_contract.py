from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest
from openpyxl import load_workbook

from steel_dxf_split import cli
from steel_dxf_split import dxf_preview
import steel_dxf_split.pipeline as pipeline
from steel_dxf_split import weld_allowance as bh_weld_allowance
from steel_dxf_split.box import compiler as box_compiler
from steel_dxf_split.box import delivery as box_delivery
from steel_dxf_split.box import weld_allowance as box_weld_allowance
from steel_dxf_split.box.contracts import BOX_EXPORT_PROFILE
from steel_dxf_split.box.release import write_box_release_attestation


def test_unified_cli_snapshots_only_the_input_directory_and_never_scans_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "a.dxf").write_bytes(b"a")
    (input_dir / "b.DXF").write_bytes(b"b")
    (input_dir / "ignore.txt").write_text("ignore", encoding="utf-8")
    calls: list[Path] = []

    class FakeResult:
        automation_route = "auto_accepted"

        def to_summary(self, **kwargs: object) -> dict[str, object]:
            return {
                "input": str(kwargs["input_path"]),
                "automation_route": "auto_accepted",
            }

    def fake_split(input_path: Path, _output: Path, _options: object) -> FakeResult:
        calls.append(input_path)
        output_dir.mkdir(exist_ok=True)
        (output_dir / f"generated-{len(calls)}.dxf").write_bytes(b"output")
        return FakeResult()

    monkeypatch.setattr(cli, "split_dxf", fake_split)

    code = cli.main(
        [
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--authorize-tekla-bh-single-part-profile",
            "tekla_bh_single_part_v1",
            "--authorize-tekla-box-single-part-profile",
            BOX_EXPORT_PROFILE,
        ]
    )

    assert code == 0
    assert calls == [input_dir / "a.dxf", input_dir / "b.DXF"]
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 2


def test_only_the_unified_main_command_is_public() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert payload["project"]["scripts"] == {
        "steel-dxf-split": "steel_dxf_split.cli:main"
    }
    assert set(pipeline.SplitOptions.__dataclass_fields__) == {
        "source_contract",
        "box_source_contract",
        "box_release_attestation",
    }


def test_unified_cli_rejects_output_nested_under_the_input_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "member.dxf").write_bytes(b"source")

    code = cli.main(
        [
            str(input_dir),
            "--output-dir",
            str(input_dir / "output"),
        ]
    )

    assert code == 2
    assert "不得相同或互相嵌套" in capsys.readouterr().err


def test_legacy_batch_and_allowance_cli_modules_are_absent_from_source_package() -> None:
    package = Path(__file__).resolve().parents[1] / "src" / "steel_dxf_split"
    retired = (
        package / "batch_cli.py",
        package / "weld_allowance_cli.py",
        package / "weld_allowance_release.py",
        package / "box" / "weld_allowance_cli.py",
        package / "box" / "weld_allowance_release.py",
    )

    assert all(not path.exists() for path in retired)
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in package.rglob("*.py")
    )
    assert "weld_allowance_cli" not in runtime_source
    assert "weld_allowance_release" not in runtime_source
    assert "batch_cli" not in runtime_source


def test_unified_cli_returns_one_when_a_task_requires_manual_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "member.dxf").write_bytes(b"source")

    class ManualReviewResult:
        def to_summary(self, **kwargs: object) -> dict[str, object]:
            return {
                "input": str(kwargs["input_path"]),
                "automation_route": "manual_review",
            }

    monkeypatch.setattr(
        cli,
        "split_dxf",
        lambda *_args, **_kwargs: ManualReviewResult(),
    )

    code = cli.main([str(input_dir), "--output-dir", str(output_dir)])

    assert code == 1
    assert json.loads(capsys.readouterr().out) == [
        {
            "input": str(input_dir / "member.dxf"),
            "automation_route": "manual_review",
        }
    ]


def test_unified_cli_returns_two_when_a_task_raises_an_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "member.dxf").write_bytes(b"source")

    def fail_split(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected hard failure")

    monkeypatch.setattr(cli, "split_dxf", fail_split)

    code = cli.main([str(input_dir), "--output-dir", str(output_dir)])

    assert code == 2
    assert json.loads(capsys.readouterr().out) == [
        {
            "input": str(input_dir / "member.dxf"),
            "compiler_version": "1.5.2",
            "automation_route": "failed",
            "error_type": "RuntimeError",
            "error": "injected hard failure",
        }
    ]


def test_real_mixed_bh_box_directory_is_processed_once_into_four_paired_dxfs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = Path(__file__).resolve().parents[1]
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    shutil.copy2(
        root / "samples" / "bh_pairs" / "2b1-cb-29_拆板前.dxf",
        input_dir / "2b1-cb-29_拆板前.dxf",
    )
    shutil.copy2(
        root
        / "samples"
        / "box_pairs"
        / "BOX_拆板前_dxf"
        / "2b1-cb-56_拆板前.dxf",
        input_dir / "2b1-cb-56_拆板前.dxf",
    )
    release_path = tmp_path / "box-release.json"
    write_box_release_attestation(
        release_path,
        pair_count=20,
        calibration_count=10,
        acceptance_count=10,
        manifest_fingerprint="a" * 64,
        gate_fingerprint="b" * 64,
    )
    monkeypatch.setattr(dxf_preview, "PREVIEW_CANVAS_PIXELS", (320, 224))
    monkeypatch.setattr(box_delivery.box_preview, "PREVIEW_DPI", 40)
    monkeypatch.setattr(
        box_delivery.box_preview,
        "PREVIEW_CANVAS_PIXELS",
        (600, 420),
    )
    monkeypatch.setattr(box_delivery.box_preview, "PREVIEW_MAX_PIXELS", 300_000)
    counts = {"detect": 0, "BH": 0, "BOX": 0, "BH_allowance": 0, "BOX_allowance": 0}
    real_detect = pipeline.detect_profile_family
    real_bh_split = pipeline.split_bh_dxf
    real_box_split = box_compiler.compile_box
    real_bh_allowance = bh_weld_allowance.apply_weld_allowance
    real_box_allowance = box_weld_allowance.apply_weld_allowance

    def detect(document: object) -> str:
        counts["detect"] += 1
        return real_detect(document)

    def split_bh(*args: object, **kwargs: object):
        counts["BH"] += 1
        return real_bh_split(*args, **kwargs)

    def split_box(*args: object, **kwargs: object):
        counts["BOX"] += 1
        return real_box_split(*args, **kwargs)

    def allow_bh(*args: object, **kwargs: object):
        counts["BH_allowance"] += 1
        return real_bh_allowance(*args, **kwargs)

    def allow_box(*args: object, **kwargs: object):
        counts["BOX_allowance"] += 1
        return real_box_allowance(*args, **kwargs)

    monkeypatch.setattr(pipeline, "detect_profile_family", detect)
    monkeypatch.setattr(pipeline, "split_bh_dxf", split_bh)
    monkeypatch.setattr(box_compiler, "compile_box", split_box)
    monkeypatch.setattr(bh_weld_allowance, "apply_weld_allowance", allow_bh)
    monkeypatch.setattr(box_weld_allowance, "apply_weld_allowance", allow_box)
    previous_box_manual_task = output_dir / "manual_review" / "box" / "2b1-cb-56"
    previous_box_manual_task.mkdir(parents=True)
    (previous_box_manual_task / "obsolete_candidate.dxf").write_bytes(b"obsolete")

    code = cli.main(
        [
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--authorize-tekla-bh-single-part-profile",
            "project_tekla_bh_dxf_v1",
            "--authorize-tekla-box-single-part-profile",
            BOX_EXPORT_PROFILE,
            "--box-release-attestation",
            str(release_path),
        ]
    )

    assert code == 0
    assert counts == {
        "detect": 2,
        "BH": 1,
        "BOX": 1,
        "BH_allowance": 1,
        "BOX_allowance": 1,
    }
    payload = json.loads(capsys.readouterr().out)
    assert {item["family"] for item in payload} == {"BH", "BOX"}
    assert len(tuple(output_dir.rglob("*.dxf"))) == 4
    assert len(tuple((output_dir / "auto_accepted" / "bh").rglob("*.dxf"))) == 2
    ledger_path = output_dir / "BH拆板信息表.xlsx"
    assert ledger_path.is_file()
    ledger = load_workbook(ledger_path, read_only=True, data_only=True)
    try:
        rows = list(ledger["BH拆板信息"].values)
    finally:
        ledger.close()
    assert rows[0] == ("零件号", "BH尺寸", "上下翼板是否相同")
    assert len(rows) == 2
    assert rows[1][2] in {"是", "否"}
    box_task = output_dir / "auto_accepted" / "box" / "2b1-cb-56"
    assert sorted(path.name for path in box_task.glob("*.dxf")) == [
        "2b1-cb-56_余量增长.dxf",
        "2b1-cb-56_正常拆板.dxf",
    ]
    assert sorted(path.name for path in box_task.glob("*.json")) == [
        "2b1-cb-56_report.json",
        "2b1-cb-56_weld_allowance_report.json",
    ]
    box_summary = next(item for item in payload if item["family"] == "BOX")
    assert box_summary["automation_route"] == "auto_accepted"
    assert not previous_box_manual_task.exists()
    assert not tuple(output_dir.glob(".steel-dxf-task-*"))
    assert len(tuple(input_dir.glob("*.dxf"))) == 2
