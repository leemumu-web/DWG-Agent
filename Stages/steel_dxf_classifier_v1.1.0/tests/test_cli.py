import json
from pathlib import Path

import ezdxf

from steel_dxf_classifier.cli import main


def make_part(path: Path, profile: str | None) -> None:
    doc = ezdxf.new("R2010")
    doc.modelspace().add_text("截面", dxfattribs={"insert": (80, 95), "height": 3})
    if profile is not None:
        doc.modelspace().add_text(
            profile,
            dxfattribs={"insert": (78, 85), "height": 3},
        )
    doc.modelspace().add_text("图纸", dxfattribs={"insert": (0, 0), "height": 2})
    doc.saveas(path)


def test_cli_returns_zero_for_fully_classified_directory(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "项目_dxf"
    source.mkdir()
    make_part(source / "part.dxf", "BOX300*300*10*10")

    code = main([str(source)])

    assert code == 0
    output = capsys.readouterr().out
    assert "已分类: 1" in output
    assert "BOX: 1" in output


def test_cli_returns_two_when_manual_review_is_required(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "项目_dxf"
    source.mkdir()
    make_part(source / "part.dxf", None)

    code = main([str(source)])

    assert code == 2
    assert "待确认: 1" in capsys.readouterr().out


def test_cli_json_mode_emits_one_completed_summary_object(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "项目_dxf"
    source.mkdir()
    make_part(source / "part.dxf", "BOX300*300*10*10")

    code = main(["--json", str(source)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert captured.out.count("\n") == 1
    assert captured.err == ""
    assert payload["schema"] == "STEEL-DXF-CLI-1.2"
    assert payload["status"] == "completed"
    assert payload["exit_code"] == 0
    assert payload["summary"]["type_counts"] == {"BOX": 1}


def test_cli_json_mode_reports_completed_review_status(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "项目_dxf"
    source.mkdir()
    make_part(source / "part.dxf", None)

    code = main(["--json", str(source)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 2
    assert captured.err == ""
    assert payload["status"] == "completed_with_review"
    assert payload["exit_code"] == 2
    assert payload["summary"]["review_required_count"] == 1


def test_cli_reports_invalid_input_without_traceback(tmp_path: Path, capsys) -> None:
    source = tmp_path / "wrong-name"
    source.mkdir()

    code = main([str(source)])

    captured = capsys.readouterr()
    assert code == 64
    assert captured.out == ""
    assert captured.err.startswith("错误:")
    assert "<项目名称>_dxf" in captured.err
    assert "Traceback" not in captured.err


def test_cli_reports_argument_error_with_usage_exit_code(capsys) -> None:
    code = main(["--unknown"])

    captured = capsys.readouterr()
    assert code == 64
    assert captured.out == ""
    assert captured.err.startswith("错误:")


def test_cli_version_is_a_stable_single_line(capsys) -> None:
    code = main(["--version"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "steel-dxf-classifier 1.2.0\n"
    assert captured.err == ""


def test_cli_overwrite_flag_allows_safe_rerun(tmp_path: Path) -> None:
    source = tmp_path / "项目_dxf"
    source.mkdir()
    make_part(source / "part.dxf", "PL20*300")

    assert main([str(source)]) == 0
    assert main([str(source)]) == 1
    assert main([str(source), "--overwrite"]) == 0
