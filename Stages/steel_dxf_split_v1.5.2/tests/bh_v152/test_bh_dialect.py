from __future__ import annotations

from dataclasses import fields, replace
import inspect
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.bh_compiler import BHCompiler, compile_bh_document
from steel_dxf_split.bh_dialect import DEFAULT_TEKLA_DIALECT
from steel_dxf_split.bh_frontend import build_bh_document_ir
from steel_dxf_split.bh_geometry import solid_part_entities
from steel_dxf_split.bh_ir import SemanticLayer, VisibilityClass
from steel_dxf_split.bh_knowledge import (
    BHSourceContract,
    DEFAULT_BH_KNOWLEDGE,
)
from steel_dxf_split.bh_release_evidence import resolve_release_evidence
from steel_dxf_split.bh_solver import runtime_instances
from steel_dxf_split.bh_semantics import part_blocks_from_ir
from steel_dxf_split.dxf_io import load_document


ROOT = Path(__file__).resolve().parents[2]
AUTO_SOURCE = ROOT / "samples" / "bh_pairs" / "2b1-cb-26_拆板前.dxf"


def test_default_knowledge_declares_the_authoritative_tekla_bh_source_contract() -> None:
    contract = DEFAULT_BH_KNOWLEDGE.source_contract

    assert contract.source_system == "tekla_structures"
    assert contract.drawing_kind == "single_part_drawing"
    assert contract.member_family == "welded_bh"
    assert contract.export_profile == "project_tekla_bh_dxf_v1"


def test_default_dialect_is_bound_to_the_project_tekla_export_profile() -> None:
    assert DEFAULT_TEKLA_DIALECT.profile_id == "project_tekla_bh_dxf_v1"


@pytest.mark.parametrize("linetype", ("XKITLINE04", "xkitline04", "DOT2", "dot2"))
def test_tekla_dialect_recognizes_equivalent_hidden_projection_linetypes(
    linetype: str,
) -> None:
    assert DEFAULT_TEKLA_DIALECT.is_hidden_projection_linetype(linetype)


@pytest.mark.parametrize("linetype", ("Continuous", "XKITLINE00", "HIDDEN2"))
def test_tekla_dialect_does_not_guess_unknown_linetypes_are_hidden(
    linetype: str,
) -> None:
    assert not DEFAULT_TEKLA_DIALECT.is_hidden_projection_linetype(linetype)


def test_release_trust_cannot_be_extended_by_ordinary_knowledge_configuration() -> None:
    assert "verified_release_profile_ids" not in {
        item.name for item in fields(type(DEFAULT_BH_KNOWLEDGE))
    }
    evidence = resolve_release_evidence(
        DEFAULT_BH_KNOWLEDGE.source_contract,
        DEFAULT_BH_KNOWLEDGE.dialect,
        DEFAULT_BH_KNOWLEDGE.ontology_version,
    )
    assert evidence is not None
    assert evidence.capability_artifact_sha256 == (
        "243fa7d095cf9c402ffcb62ad03634b0e25b895c2fa3ea6af6004b1d5fdc2e34"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_system", "generic_cad"),
        ("drawing_kind", "assembly_drawing"),
        ("member_family", "rolled_h"),
    ),
)
def test_compiler_rejects_an_unsupported_source_contract_before_parsing(
    field: str,
    value: str,
) -> None:
    contract = replace(
        DEFAULT_BH_KNOWLEDGE.source_contract,
        **{field: value},
    )
    knowledge = replace(DEFAULT_BH_KNOWLEDGE, source_contract=contract)

    with pytest.raises(ValueError, match="BH source contract violation"):
        BHCompiler(passes=(), knowledge=knowledge).compile(
            ezdxf.new(),
            source_contract=contract,
        )


def test_compiler_rejects_contract_dialect_profile_mismatch() -> None:
    contract = replace(
        DEFAULT_BH_KNOWLEDGE.source_contract,
        export_profile="another_tekla_export_profile",
    )
    knowledge = replace(DEFAULT_BH_KNOWLEDGE, source_contract=contract)

    with pytest.raises(ValueError, match="export_profile"):
        BHCompiler(knowledge=knowledge).compile(
            ezdxf.new(),
            source_contract=contract,
        )


def test_public_compiler_requires_an_explicit_workflow_source_contract() -> None:
    parameters = inspect.signature(compile_bh_document).parameters

    assert "source_contract" in parameters
    assert parameters["source_contract"].default is inspect.Parameter.empty


def test_matching_explicit_tekla_contract_accepts_configured_layer_aliases() -> None:
    dialect = DEFAULT_TEKLA_DIALECT.with_alias(
        SemanticLayer.PART_EDGE,
        "CUTTING_EDGE",
    )
    contract = BHSourceContract(export_profile=dialect.profile_id)

    contract.validate(dialect)


def test_matching_but_unverified_tekla_profile_cannot_auto_accept() -> None:
    dialect = replace(
        DEFAULT_TEKLA_DIALECT,
        profile_id="project_tekla_bh_dxf_v2",
    )
    contract = BHSourceContract(export_profile=dialect.profile_id)
    knowledge = replace(
        DEFAULT_BH_KNOWLEDGE,
        dialect=dialect,
        source_contract=contract,
    )

    compiled = BHCompiler(knowledge=knowledge).compile(
        load_document(AUTO_SOURCE),
        source_contract=contract,
    )

    assert compiled.assessment.disposition.value == "review_required"
    assert (
        "BH.PROOF.SOURCE.RELEASE_PROFILE_VERIFIED"
        in compiled.proof_report.blocking_obligation_ids
    )


def test_tekla_roles_are_case_insensitive() -> None:
    assert (
        DEFAULT_TEKLA_DIALECT.hint("PART", "LINE", "BYLAYER").role
        == SemanticLayer.PART_EDGE
    )
    assert (
        DEFAULT_TEKLA_DIALECT.hint("bolt", "CIRCLE", "BYLAYER").role
        == SemanticLayer.PHYSICAL_CUT
    )
    assert (
        DEFAULT_TEKLA_DIALECT.hint("BoLt", "POINT", "BYLAYER").role
        == SemanticLayer.CUT_HELPER
    )
    assert (
        DEFAULT_TEKLA_DIALECT.hint(
            "z-dimensions-lines", "LINE", "Continuous"
        ).role
        == SemanticLayer.DIMENSION
    )


def test_similarly_named_unknown_layer_is_not_guessed_to_be_dimension() -> None:
    assert (
        DEFAULT_TEKLA_DIALECT.hint(
            "Z-DIMENSIONLESS-GEOMETRY", "LINE", "Continuous"
        ).role
        == SemanticLayer.UNKNOWN
    )


def test_configured_alias_is_a_hint_not_a_manufacturing_feature() -> None:
    resolver = DEFAULT_TEKLA_DIALECT.with_alias(
        SemanticLayer.PART_EDGE,
        "CUTTING_EDGE",
    )

    line_hint = resolver.hint("cutting_edge", "LINE", "BYLAYER")
    text_hint = resolver.hint("cutting_edge", "TEXT", "BYLAYER")

    assert line_hint.role == SemanticLayer.PART_EDGE
    assert line_hint.confidence == 1.0
    assert text_hint.role == SemanticLayer.UNKNOWN
    assert text_hint.reason == "entity_type_incompatible"


def test_unknown_layer_stays_unknown() -> None:
    hint = DEFAULT_TEKLA_DIALECT.hint("CUSTOM", "LINE", "BYLAYER")
    assert hint.role == SemanticLayer.UNKNOWN
    assert hint.confidence == 0.0
    assert hint.reason == "layer_unmapped"


def test_dialect_profile_is_immutable_when_alias_is_added() -> None:
    extended = DEFAULT_TEKLA_DIALECT.with_alias(
        SemanticLayer.DIMENSION,
        "MY_DIMENSIONS",
    )

    assert (
        DEFAULT_TEKLA_DIALECT.hint("MY_DIMENSIONS", "LINE", "BYLAYER").role
        == SemanticLayer.UNKNOWN
    )
    assert (
        extended.hint("my_dimensions", "LINE", "BYLAYER").role
        == SemanticLayer.DIMENSION
    )


def test_lowering_ir_receives_canonical_layer_without_losing_source_spelling() -> None:
    doc = ezdxf.new()
    doc.layers.add("PART")
    block = doc.blocks.new("VIEW")
    block.add_line((0, 0), (100, 0), dxfattribs={"layer": "PART"})
    doc.modelspace().add_blockref("VIEW", (0, 0))

    ir = build_bh_document_ir(doc)
    source_atom = ir.blocks[0].entities[0]
    runtime = runtime_instances(ir)

    assert source_atom.source.layer == "PART"
    assert source_atom.entity.dxf.layer == "PART"
    assert runtime[0].entities[0].dxf.layer == "Part"


@pytest.mark.parametrize("source_linetype", ("XKITLINE04", "DOT2"))
def test_hidden_part_projection_aliases_lower_to_one_runtime_semantic(
    source_linetype: str,
) -> None:
    doc = ezdxf.new()
    doc.layers.add("Part")
    doc.linetypes.add(source_linetype, pattern=[0.5, 0.0, -0.5])
    block = doc.blocks.new("VIEW")
    block.add_line(
        (0, 0),
        (100, 0),
        dxfattribs={"layer": "Part", "linetype": source_linetype},
    )
    doc.modelspace().add_blockref("VIEW", (0, 0))

    ir = build_bh_document_ir(doc)
    source_atom = ir.blocks[0].entities[0]
    runtime_entity = runtime_instances(ir)[0].entities[0]

    assert source_atom.source.linetype == source_linetype
    assert source_atom.visibility == VisibilityClass.HIDDEN
    assert runtime_entity.dxf.linetype == "XKITLINE04"
    assert solid_part_entities([runtime_entity]) == []


def test_continuous_part_edge_remains_physical_after_runtime_lowering() -> None:
    doc = ezdxf.new()
    doc.layers.add("Part")
    block = doc.blocks.new("VIEW")
    block.add_line(
        (0, 0),
        (100, 0),
        dxfattribs={"layer": "Part", "linetype": "Continuous"},
    )
    doc.modelspace().add_blockref("VIEW", (0, 0))

    ir = build_bh_document_ir(doc)
    source_atom = ir.blocks[0].entities[0]
    runtime_entity = runtime_instances(ir)[0].entities[0]

    assert source_atom.visibility == VisibilityClass.PHYSICAL
    assert runtime_entity.dxf.linetype == "Continuous"
    assert solid_part_entities([runtime_entity]) == [runtime_entity]


def test_hidden_projection_line_cannot_split_a_physical_part_face() -> None:
    doc = ezdxf.new()
    doc.layers.add("Part")
    doc.linetypes.add("DOT2", pattern=[0.5, 0.0, -0.5])
    block = doc.blocks.new("VIEW")
    for start, end in (
        ((0, 0), (100, 0)),
        ((100, 0), (100, 50)),
        ((100, 50), (0, 50)),
        ((0, 50), (0, 0)),
    ):
        block.add_line(start, end, dxfattribs={"layer": "Part"})
    block.add_line(
        (50, 0),
        (50, 50),
        dxfattribs={"layer": "Part", "linetype": "DOT2"},
    )
    doc.modelspace().add_blockref("VIEW", (0, 0))

    part_block = part_blocks_from_ir(build_bh_document_ir(doc))[0]

    assert [entity.dxf.linetype for entity in part_block.entities].count(
        "XKITLINE04"
    ) == 1
    assert len(solid_part_entities(part_block.entities)) == 4
