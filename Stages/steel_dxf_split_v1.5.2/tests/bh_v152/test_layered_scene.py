from steel_dxf_split.bh_trace import InMemoryTraceObserver, TraceShape
from steel_dxf_split.layered_scene import scene_from_event


def test_scene_preserves_geometry_identity_and_adds_intermediate_warning() -> None:
    observer = InMemoryTraceObserver("beam")
    event = observer.emit(
        stage_id="05_candidate_lowering",
        artifact_id="web_faces",
        status="observed",
        title_zh="腹板面域",
        summary_zh="识别 1 个候选面",
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
    scene = scene_from_event(event)
    assert scene.scene_id == "0001-web_faces"
    assert scene.shapes[0].shape_id == "face-1"
    assert scene.warning == "INTERMEDIATE EVIDENCE / 非生产下料"
    assert scene.bounds == (0.0, 0.0, 5.0, 2.0)
    assert scene.metrics == (("grid_size_mm", "0.001"),)


def test_scene_bounds_include_circle_radius() -> None:
    observer = InMemoryTraceObserver("beam")
    event = observer.emit(
        stage_id="05_candidate_lowering",
        artifact_id="cuts",
        status="observed",
        title_zh="圆孔",
        summary_zh="一个圆孔",
        shapes=(
            TraceShape(
                "cut-1",
                "circle",
                "physical_cut",
                ((10.0, 20.0),),
                properties={"radius": 3.0},
            ),
        ),
        payload={},
    )
    assert scene_from_event(event).bounds == (7.0, 17.0, 13.0, 23.0)


def test_not_applicable_scene_remains_renderable_and_explains_reason() -> None:
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
    assert scene.bounds == (0.0, 0.0, 1.0, 1.0)
    assert scene.metrics == (("reason", "no_dimension_entities"),)
    assert scene.status == "not_applicable"


def test_scene_expands_a_collapsed_axis_for_single_line_rendering() -> None:
    observer = InMemoryTraceObserver("beam")
    event = observer.emit(
        stage_id="01_frontend_fact_ir",
        artifact_id="single_line",
        status="observed",
        title_zh="单线",
        summary_zh="水平线也必须具有可渲染视口",
        shapes=(
            TraceShape(
                "line-1",
                "line",
                "part_visible",
                ((0.0, 2.0), (5.0, 2.0)),
            ),
        ),
        payload={},
    )
    assert scene_from_event(event).bounds == (0.0, 1.5, 5.0, 2.5)
