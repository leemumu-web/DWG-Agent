from steel_dxf_split.bh_trace import STAGE_REGISTRY, InMemoryTraceObserver, TraceShape


def test_stage_registry_is_complete_and_ordered() -> None:
    assert [item.stage_id for item in STAGE_REGISTRY] == [
        "00_input_provenance",
        "01_frontend_fact_ir",
        "02_annotation_facts",
        "03_metadata_semantics",
        "04_view_hypothesis_frontier",
        "05_candidate_lowering",
        "06_constraints_and_selection",
        "07_assembly_validation",
        "08_manufacturing_ir",
        "09_quality_route",
        "10_codegen_layout",
        "11_saved_output_validation",
        "12_manual_supervision",
        "13_corpus_summary",
    ]


def test_observer_assigns_stable_sequence_and_serializes_shapes() -> None:
    observer = InMemoryTraceObserver(sample_id="beam-1")
    first = observer.emit(
        stage_id="01_frontend_fact_ir",
        artifact_id="semantic_layers",
        status="observed",
        title_zh="实体语义分类",
        summary_zh="Part 与 Bolt 已分类",
        shapes=(
            TraceShape(
                "line-1",
                "line",
                "part_visible",
                ((0.0, 0.0), (5.0, 0.0)),
            ),
        ),
        payload={"count": 1},
    )
    second = observer.emit(
        stage_id="02_annotation_facts",
        artifact_id="dimensions",
        status="not_applicable",
        title_zh="尺寸事实",
        summary_zh="未观察到尺寸",
        payload={"reason": "no_dimension_entities"},
    )
    assert (first.sequence, second.sequence) == (1, 2)
    assert observer.to_dict()["events"][0]["shapes"][0]["shape_id"] == "line-1"
