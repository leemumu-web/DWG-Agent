from __future__ import annotations

import hashlib
import json
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_classifier.batch import classify_directory


def make_part_dxf(path: Path, profile: str | None) -> None:
    doc = ezdxf.new("R2010")
    block = doc.blocks.new("TITLE")
    block.add_text("截面", dxfattribs={"insert": (80, 95), "height": 3})
    if profile is not None:
        block.add_text(profile, dxfattribs={"insert": (78, 85), "height": 3})
    doc.modelspace().add_blockref("TITLE", (0, 0))
    doc.modelspace().add_text("图纸", dxfattribs={"insert": (0, 0), "height": 2})
    doc.saveas(path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_batch_copies_only_first_level_files_and_routes_failures(tmp_path: Path) -> None:
    source = tmp_path / "项目2_dxf"
    source.mkdir()
    make_part_dxf(source / "bh.dxf", "BH300*200*6*8")
    make_part_dxf(source / "review.dxf", None)
    (source / "broken.dxf").write_text("not dxf", encoding="utf-8")
    nested = source / "nested"
    nested.mkdir()
    make_part_dxf(nested / "ignored.dxf", "BOX300*300*10*10")
    before = {path.stem: digest(path) for path in source.glob("*.dxf")}

    summary = classify_directory(source)

    assert (tmp_path / "项目2_BH_dxf" / "bh_拆板前.dxf").is_file()
    assert (tmp_path / "项目2_待确认_dxf" / "review_拆板前.dxf").is_file()
    assert (tmp_path / "项目2_无法读取_dxf" / "broken_拆板前.dxf").is_file()
    assert not (tmp_path / "项目2_BOX_dxf").exists()
    assert summary.input_count == 3
    assert summary.classified_count == 1
    assert summary.review_required_count == 1
    assert summary.unreadable_count == 1
    assert before == {
        path.name.removesuffix("_拆板前.dxf"): digest(path)
        for path in source.glob("*.dxf")
    }


def test_batch_preprocesses_input_name_before_classification(tmp_path: Path) -> None:
    source = tmp_path / "预处理项目_dxf"
    source.mkdir()
    make_part_dxf(source / "part.dxf", "BH300*200*6*8")
    original_digest = digest(source / "part.dxf")

    classify_directory(source)

    renamed = source / "part_拆板前.dxf"
    routed = tmp_path / "预处理项目_BH_dxf" / "part_拆板前.dxf"
    report = json.loads(
        (tmp_path / "预处理项目_分类报告.json").read_text(encoding="utf-8")
    )
    assert renamed.is_file()
    assert routed.is_file()
    assert not (source / "part.dxf").exists()
    assert digest(renamed) == digest(routed) == original_digest
    assert report["results"][0]["source_name"] == "part_拆板前.dxf"


def test_batch_writes_consistent_json_and_csv_reports(tmp_path: Path) -> None:
    source = tmp_path / "项目A_dxf"
    source.mkdir()
    make_part_dxf(source / "零件一.dxf", "RHS200*100*8")

    summary = classify_directory(source)
    report = json.loads((tmp_path / "项目A_分类报告.json").read_text(encoding="utf-8"))
    csv_text = (tmp_path / "项目A_分类清单.csv").read_text(encoding="utf-8-sig")

    assert report["schema"] == "STEEL-DXF-CLASSIFICATION-1.1"
    assert report["summary"]["input_count"] == summary.input_count == 1
    assert report["results"][0]["part_type"] == "RHS"
    assert report["results"][0]["output_directory"] == "项目A_RHS_dxf"
    assert "零件一_拆板前.dxf" in csv_text
    assert "TITLE_PROFILE_PROVED" in csv_text


def test_batch_routes_xbox_and_bbh_to_independent_directories(tmp_path: Path) -> None:
    source = tmp_path / "组合项目_dxf"
    source.mkdir()
    make_part_dxf(source / "xbox.dxf", "XBOX300*300*10*10")
    make_part_dxf(source / "bbh.dxf", "BBH600*200*12*22")

    summary = classify_directory(source)

    assert (tmp_path / "组合项目_XBOX_dxf" / "xbox_拆板前.dxf").is_file()
    assert (tmp_path / "组合项目_BBH_dxf" / "bbh_拆板前.dxf").is_file()
    assert summary.type_counts == {"BBH": 1, "XBOX": 1}


def test_batch_refuses_existing_outputs_without_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "P_dxf"
    source.mkdir()
    make_part_dxf(source / "part.dxf", "PL20*300")
    classify_directory(source)

    with pytest.raises(FileExistsError, match="--overwrite"):
        classify_directory(source)

    second = classify_directory(source, overwrite=True)
    assert second.classified_count == 1
    assert (tmp_path / "P_PL_dxf" / "part_拆板前.dxf").is_file()


@pytest.mark.parametrize("name", ["项目2", "_dxf", "项目2_DXF"])
def test_batch_requires_exact_project_directory_suffix(tmp_path: Path, name: str) -> None:
    source = tmp_path / name
    source.mkdir()

    with pytest.raises(ValueError, match="<项目名称>_dxf"):
        classify_directory(source)
