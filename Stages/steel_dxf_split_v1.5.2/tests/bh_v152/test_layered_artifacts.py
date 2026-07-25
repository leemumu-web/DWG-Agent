from pathlib import Path

from steel_dxf_split.bh_trace import InMemoryTraceObserver, TraceShape
from steel_dxf_split.layered_artifacts import LayeredArchive
from steel_dxf_split.layered_dxf import render_scene_dxf
from steel_dxf_split.layered_scene import scene_from_event


def test_archive_writes_mirrored_json_dxf_svg_and_validates_hashes(
    tmp_path: Path,
) -> None:
    observer = InMemoryTraceObserver("beam")
    sample_event = observer.emit(
        stage_id="05_candidate_lowering",
        artifact_id="web_faces",
        status="observed",
        title_zh="腹板面域",
        summary_zh="识别候选面",
        hypothesis_id="assembly-01",
        shapes=(
            TraceShape(
                "face-1",
                "polygon",
                "face_candidate",
                ((0.0, 0.0), (5.0, 0.0), (5.0, 2.0), (0.0, 2.0), (0.0, 0.0)),
                True,
            ),
        ),
        payload={"grid_size_mm": 0.001},
    )
    archive = LayeredArchive(tmp_path)
    item = archive.write_event(sample_event)
    assert item.dxf_path == Path(
        "dxf/intermediate/beam/05_candidate_lowering/assembly-01/0001-web_faces.dxf"
    )
    assert item.svg_path == Path(
        "svg/intermediate/beam/05_candidate_lowering/assembly-01/0001-web_faces.svg"
    )
    assert item.json_path == Path(
        "json/beam/05_candidate_lowering/assembly-01/0001-web_faces.json"
    )
    report = archive.validate()
    assert report.ok
    assert report.missing == []
    assert report.orphans == []


def test_archive_renders_not_applicable_reason_in_both_formats(tmp_path: Path) -> None:
    observer = InMemoryTraceObserver("beam")
    event = observer.emit(
        stage_id="02_annotation_facts",
        artifact_id="dimensions",
        status="not_applicable",
        title_zh="尺寸事实",
        summary_zh="未观察到尺寸",
        payload={"reason": "no_dimension_entities"},
    )
    archive = LayeredArchive(tmp_path)
    item = archive.write_event(event)
    assert "no_dimension_entities" in (tmp_path / item.svg_path).read_text(
        encoding="utf-8"
    )
    assert (tmp_path / item.dxf_path).exists()
    assert archive.validate().ok


def test_archive_detects_orphan_and_hash_change(tmp_path: Path) -> None:
    observer = InMemoryTraceObserver("beam")
    event = observer.emit(
        stage_id="02_annotation_facts",
        artifact_id="dimensions",
        status="not_applicable",
        title_zh="尺寸事实",
        summary_zh="未观察到尺寸",
        payload={"reason": "no_dimension_entities"},
    )
    archive = LayeredArchive(tmp_path)
    item = archive.write_event(event)
    (tmp_path / item.svg_path).write_text("tampered", encoding="utf-8")
    orphan = tmp_path / "dxf/intermediate/orphan.dxf"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("orphan", encoding="utf-8")
    report = archive.validate()
    assert not report.ok
    assert item.svg_path.as_posix() in report.hash_mismatches
    assert Path("dxf/intermediate/orphan.dxf") in report.orphans


def test_all_archive_categories_keep_separate_mirrored_dxf_svg_trees(
    tmp_path: Path,
) -> None:
    observer = InMemoryTraceObserver("beam")
    event = observer.emit(
        stage_id="13_corpus_summary",
        artifact_id="summary",
        status="observed",
        title_zh="汇总",
        summary_zh="分类归档",
        shapes=(),
        payload={},
    )
    scene = scene_from_event(event)
    source_dxf = render_scene_dxf(scene, tmp_path / "source.dxf")
    archive = LayeredArchive(tmp_path)
    for category in ("final", "reference", "comparison", "corpus"):
        item = archive.write_scene_pair(
            scene,
            category=category,
            dxf_source=source_dxf,
            json_payload={"category": category},
            filename=f"{category}-artifact",
        )
        assert item.dxf_path.parts[:2] == ("dxf", category)
        assert item.svg_path.parts[:2] == ("svg", category)
        assert item.dxf_path.with_suffix("").parts[1:] == item.svg_path.with_suffix("").parts[1:]
    assert archive.validate().ok
