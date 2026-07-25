from __future__ import annotations

from collections import Counter
from pathlib import Path

import ezdxf
import pytest
from ezdxf.math import Matrix44

from steel_dxf_split.bh_annotations import AnnotationModel, BoltMarkObservation
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_extractor import BHBlockInstance
from steel_dxf_split.bh_fingerprint import manufacturing_payload
from steel_dxf_split.bh_geometry import PartBlock, entities_bbox
from steel_dxf_split.bh_ir import SourceViewRef
from steel_dxf_split.bh_knowledge import (
    BHUniformScalePolicy,
    DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
)
from steel_dxf_split.bh_metric_scale import (
    resolve_view_metric_scale,
    scale_part_block,
    scale_runtime_instances,
)
from steel_dxf_split.bh_models import BHMetadata, HProfile
from steel_dxf_split.dxf_io import load_document

from bh_transform_fixtures import transform_modelspace


ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"


def _assert_nested_close(actual, expected, *, path: str = "root") -> None:
    assert type(actual) is type(expected), path
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), path
        for key in expected:
            _assert_nested_close(
                actual[key],
                expected[key],
                path=f"{path}.{key}",
            )
        return
    if isinstance(expected, list):
        assert len(actual) == len(expected), path
        for index, value in enumerate(expected):
            _assert_nested_close(
                actual[index],
                value,
                path=f"{path}[{index}]",
            )
        return
    if isinstance(expected, float):
        assert actual == pytest.approx(expected, abs=1e-5), path
        return
    assert actual == expected, path


def _rectangle(msp, *, width: float, height: float):
    return [
        msp.add_line((0, 0), (width, 0), dxfattribs={"layer": "Part"}),
        msp.add_line((width, 0), (width, height), dxfattribs={"layer": "Part"}),
        msp.add_line((width, height), (0, height), dxfattribs={"layer": "Part"}),
        msp.add_line((0, height), (0, 0), dxfattribs={"layer": "Part"}),
    ]


def _part_block(doc, name: str, region_id: str, width: float, height: float) -> PartBlock:
    if name not in doc.blocks:
        doc.blocks.new(name)
    insert = doc.modelspace().add_blockref(name, (0, 0))
    entities = _rectangle(doc.modelspace(), width=width, height=height)
    return PartBlock(
        insert=insert,
        entities=entities,
        bbox=entities_bbox(entities),
        source_view=SourceViewRef(
            region_id=region_id,
            geometry_signature=f"signature-{region_id}",
            source_ids=(f"edge-{region_id}",),
            container_ids=(f"container-{region_id}",),
            explicit_block=True,
        ),
        entity_source_ids=tuple(f"{region_id}-edge-{index}" for index in range(4)),
    )


def _case(
    source_scale: float,
    *,
    main_transverse_scale: float | None = None,
    include_bolt_mark: bool = True,
    bolt_source_scale: float | None = None,
    bolt_layer: str = "Bolt",
):
    length = 1000.0
    height = 600.0
    flange_width = 240.0
    transverse_scale = (
        source_scale
        if main_transverse_scale is None
        else main_transverse_scale
    )
    doc = ezdxf.new()
    main = _part_block(
        doc,
        "MAIN",
        "region-main",
        length * source_scale,
        height * transverse_scale,
    )
    flange = _part_block(
        doc,
        "FLANGE",
        "region-flange",
        length * source_scale,
        flange_width * source_scale,
    )
    circle_scale = source_scale if bolt_source_scale is None else bolt_source_scale
    circle = doc.modelspace().add_circle(
        (100.0 * source_scale, 100.0 * transverse_scale),
        radius=13.0 * circle_scale,
        dxfattribs={"layer": bolt_layer},
    )
    if "BOLT_AUX" not in doc.blocks:
        doc.blocks.new("BOLT_AUX")
    instance = BHBlockInstance(
        insert=doc.modelspace().add_blockref("BOLT_AUX", (0, 0)),
        entities=[circle],
        layer_counts=Counter({"Bolt": 1}),
        texts=[],
        entity_source_ids=("hole-1",),
    )
    annotations = AnnotationModel(
        bolt_marks=(
            [
                BoltMarkObservation(
                    raw_text="1Φ26",
                    count=1,
                    diameter=26.0,
                    block_name="BOLT_MARK",
                    block_handle="mark-1",
                    target_source_ids=("hole-1",),
                    target_region_ids=("region-main",),
                )
            ]
            if include_bolt_mark
            else []
        )
    )
    metadata = BHMetadata(
        part_number="test-bh",
        profile=HProfile(
            height=height,
            flange_width=flange_width,
            web_thickness=12.0,
            flange_thickness=20.0,
            raw_text="BH600*240*12*20",
        ),
        nominal_length=length,
        material="Q355C",
        drawing_scale=1.0,
    )
    return main, flange, metadata, annotations, [instance]


@pytest.mark.parametrize(
    ("source_scale", "expected_factor"),
    [(0.5, 2.0), (1.5, 2.0 / 3.0), (2.0, 0.5)],
)
def test_non_identity_scale_requires_consistent_dimensions_and_hole_diameter(
    source_scale: float,
    expected_factor: float,
) -> None:
    main, flange, metadata, annotations, instances = _case(source_scale)

    result = resolve_view_metric_scale(
        main,
        flange,
        metadata,
        annotations,
        instances,
        BHUniformScalePolicy(),
    )

    assert result.mode == "normalized"
    assert result.factor == pytest.approx(expected_factor)
    assert {item.channel for item in result.evidence} == {
        "main_longitudinal",
        "main_transverse",
        "flange_longitudinal",
        "flange_transverse",
        "bolt_diameter",
    }


def test_identity_scale_preserves_normal_drawing_without_bolt_mark() -> None:
    main, flange, metadata, annotations, instances = _case(
        1.0,
        include_bolt_mark=False,
    )

    result = resolve_view_metric_scale(
        main,
        flange,
        metadata,
        annotations,
        instances,
        BHUniformScalePolicy(),
    )

    assert result.mode == "identity"
    assert result.factor == 1.0


def test_identity_scale_does_not_reclassify_a_legitimate_web_envelope() -> None:
    main, flange, metadata, annotations, instances = _case(
        1.0,
        main_transverse_scale=1.10,
    )

    result = resolve_view_metric_scale(
        main,
        flange,
        metadata,
        annotations,
        instances,
        BHUniformScalePolicy(),
    )

    assert result.mode == "identity"
    assert result.factor == 1.0


def test_non_identity_scale_accepts_canonical_layer_case_variants() -> None:
    main, flange, metadata, annotations, instances = _case(
        0.5,
        bolt_layer="BOLT",
    )

    result = resolve_view_metric_scale(
        main,
        flange,
        metadata,
        annotations,
        instances,
        BHUniformScalePolicy(),
    )

    assert result.mode == "normalized"
    assert result.factor == pytest.approx(2.0)


def test_non_identity_scale_rejects_unbound_global_bolt_evidence() -> None:
    main, flange, metadata, annotations, instances = _case(0.5)
    main.source_view = None
    flange.source_view = None

    result = resolve_view_metric_scale(
        main,
        flange,
        metadata,
        annotations,
        instances,
        BHUniformScalePolicy(),
    )

    assert result.mode == "blocked"
    assert result.reason == "missing_bound_bolt_diameter_evidence"


def test_opposing_non_identity_view_scales_cannot_average_to_identity() -> None:
    main, flange, metadata, annotations, instances = _case(2.0)
    flange = scale_part_block(flange, 1.0 / 3.0)

    result = resolve_view_metric_scale(
        main,
        flange,
        metadata,
        annotations,
        instances,
        BHUniformScalePolicy(),
    )

    assert result.mode == "blocked"
    assert result.factor == 1.0


@pytest.mark.parametrize(
    "case_kwargs",
    [
        {"source_scale": 0.5, "include_bolt_mark": False},
        {"source_scale": 0.5, "bolt_source_scale": 1.0},
        {"source_scale": 0.5, "main_transverse_scale": 0.75},
    ],
)
def test_non_identity_scale_is_blocked_without_complete_consensus(
    case_kwargs: dict[str, float | bool],
) -> None:
    main, flange, metadata, annotations, instances = _case(**case_kwargs)

    result = resolve_view_metric_scale(
        main,
        flange,
        metadata,
        annotations,
        instances,
        BHUniformScalePolicy(),
    )

    assert result.mode == "blocked"
    assert result.factor == 1.0


def test_authorized_factor_scales_part_edges_and_runtime_holes_together() -> None:
    main, _, _, _, instances = _case(0.5)

    scaled_main = scale_part_block(main, 2.0)
    scaled_instances = scale_runtime_instances(instances, 2.0)

    assert scaled_main.bbox.width == pytest.approx(1000.0)
    assert scaled_main.bbox.height == pytest.approx(600.0)
    assert scaled_instances[0].entities[0].dxf.radius == pytest.approx(13.0)
    assert scaled_instances[0].entity_source_ids == instances[0].entity_source_ids


def test_uniformly_scaled_source_uses_one_solver_path_and_preserves_output() -> None:
    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"
    baseline = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    scaled = transform_modelspace(
        load_document(source),
        Matrix44.scale(0.5, 0.5, 1.0),
    )

    recovered = compile_bh_document(
        scaled,
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )

    _assert_nested_close(
        manufacturing_payload(recovered.assembly),
        manufacturing_payload(baseline.assembly),
    )
    assert recovered.hypotheses.selected.view_pair.metric_scale.mode == "normalized"
    assert recovered.hypotheses.selected.view_pair.metric_scale.factor == pytest.approx(
        2.0
    )
    assert recovered.manufacturing_validation.ok
    provenance_proof = next(
        item
        for item in recovered.proof_report.obligations
        if item.obligation_id == "BH.PROOF.MANUFACTURING_IR.PROVENANCE"
    )
    assert provenance_proof.status.value == "pass"
