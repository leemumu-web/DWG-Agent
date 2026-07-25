from pathlib import Path
from xml.etree import ElementTree

import ezdxf

from steel_dxf_split.bh_trace import InMemoryTraceObserver, TraceShape
from steel_dxf_split.layered_dxf import render_scene_dxf
from steel_dxf_split.layered_scene import scene_from_event
from steel_dxf_split.layered_svg import render_scene_svg


def test_scene_renders_to_auditable_dxf_and_parseable_svg(tmp_path: Path) -> None:
    observer = InMemoryTraceObserver("beam")
    event = observer.emit(
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
    sample_scene = scene_from_event(event)
    dxf_path = tmp_path / "scene.dxf"
    svg_path = tmp_path / "scene.svg"
    dxf_path_2 = tmp_path / "scene-copy.dxf"
    svg_path_2 = tmp_path / "scene-copy.svg"
    render_scene_dxf(sample_scene, dxf_path)
    render_scene_svg(sample_scene, svg_path)
    render_scene_dxf(sample_scene, dxf_path_2)
    render_scene_svg(sample_scene, svg_path_2)
    doc = ezdxf.readfile(dxf_path)
    assert not doc.audit().errors
    assert doc.header["$INSUNITS"] == 4
    assert "FACE_CANDIDATE" in doc.layers
    root = ElementTree.parse(svg_path).getroot()
    assert root.tag.endswith("svg")
    assert "face-1" in svg_path.read_text(encoding="utf-8")
    assert "非生产下料" in svg_path.read_text(encoding="utf-8")
    assert dxf_path.read_bytes() == dxf_path_2.read_bytes()
    assert svg_path.read_bytes() == svg_path_2.read_bytes()


def test_empty_not_applicable_scene_still_renders_both_formats(tmp_path: Path) -> None:
    observer = InMemoryTraceObserver("beam")
    event = observer.emit(
        stage_id="02_annotation_facts",
        artifact_id="dimensions",
        status="not_applicable",
        title_zh="尺寸事实",
        summary_zh="未观察到尺寸",
        payload={"reason": "no_dimension_entities"},
    )
    scene = scene_from_event(event)
    dxf_path = tmp_path / "na.dxf"
    svg_path = tmp_path / "na.svg"
    render_scene_dxf(scene, dxf_path)
    render_scene_svg(scene, svg_path)
    assert not ezdxf.readfile(dxf_path).audit().errors
    assert ElementTree.parse(svg_path).getroot().tag.endswith("svg")
    assert "no_dimension_entities" in svg_path.read_text(encoding="utf-8")


def test_renderers_cover_every_trace_primitive(tmp_path: Path) -> None:
    observer = InMemoryTraceObserver("beam")
    event = observer.emit(
        stage_id="05_candidate_lowering",
        artifact_id="all_primitives",
        status="observed",
        title_zh="全部图元",
        summary_zh="验证双格式图元覆盖",
        shapes=(
            TraceShape("line", "line", "part_visible", ((0.0, 0.0), (4.0, 0.0))),
            TraceShape(
                "bulge",
                "polyline",
                "manufacturing_plate",
                ((0.0, 1.0), (4.0, 1.0), (4.0, 2.0)),
                False,
                (0.41421356237, 0.0, 0.0),
            ),
            TraceShape(
                "polygon-with-hole",
                "polygon",
                "face_selected",
                ((0.0, 3.0), (5.0, 3.0), (5.0, 6.0), (0.0, 6.0), (0.0, 3.0)),
                True,
                properties={
                    "interiors": [
                        [(1.0, 4.0), (2.0, 4.0), (2.0, 5.0), (1.0, 5.0), (1.0, 4.0)]
                    ]
                },
            ),
            TraceShape(
                "circle",
                "circle",
                "physical_cut",
                ((7.0, 1.0),),
                properties={"radius": 0.5},
            ),
            TraceShape(
                "arc",
                "arc",
                "part_hidden",
                ((8.0, 0.0), (9.0, 1.0), (10.0, 0.0)),
                properties={
                    "center": [9.0, 0.0],
                    "radius": 1.0,
                    "start_angle": 0.0,
                    "end_angle": 180.0,
                },
            ),
            TraceShape("point", "point", "warning", ((7.0, 3.0),)),
            TraceShape(
                "text",
                "text",
                "annotation",
                ((7.0, 4.0),),
                properties={"text": "说明", "height": 0.5},
            ),
        ),
        payload={},
    )
    scene = scene_from_event(event)
    dxf_path = render_scene_dxf(scene, tmp_path / "all.dxf")
    svg_path = render_scene_svg(scene, tmp_path / "all.svg")
    doc = ezdxf.readfile(dxf_path)
    assert not doc.audit().errors
    assert len(doc.modelspace().query("ARC")) == 1
    assert len(doc.modelspace().query("CIRCLE")) == 1
    assert ElementTree.parse(svg_path).getroot().tag.endswith("svg")
