#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import gc
import hashlib
from io import StringIO
import json
from math import radians
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable
from unittest.mock import patch

import ezdxf
from ezdxf.document import Drawing
from ezdxf.math import Matrix44

from steel_dxf_split import __version__
from steel_dxf_split.bh_compare import compare_bh_to_manual
from steel_dxf_split.bh_compiler import (
    BHCompilationRejected,
    BHCompileResult,
    BHCompiler,
)
from steel_dxf_split.bh_corpus import (
    CorpusCase,
    load_corpus_manifest,
    validate_corpus_manual_file,
    validate_corpus_source_file,
)
from steel_dxf_split.bh_knowledge import (
    DEFAULT_BH_KNOWLEDGE,
    DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
)
from steel_dxf_split.bh_pipeline import split_bh_dxf
from steel_dxf_split.dxf_preview import validate_preview_pair
import steel_dxf_split.bh_release_evidence as release_evidence_module
from steel_dxf_split.bh_release_evidence import build_release_capability_payload
from steel_dxf_split.dxf_io import load_document


STRICT_MUTATIONS = {
    "translate",
    "mirror_x",
    "mirror_y",
    "uppercase_layers",
    "explode",
}
DIAGNOSTIC_MUTATIONS = {"rotate90", "rotate37"}
KNOWN_MUTATIONS = STRICT_MUTATIONS | DIAGNOSTIC_MUTATIONS
REQUIRED_RELEASE_MUTATIONS = frozenset(STRICT_MUTATIONS)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS_MANIFEST = ROOT / "tests" / "fixtures" / "bh_corpus.json"


def _release_evidence_snapshot() -> dict[str, Any]:
    evidence = release_evidence_module.resolve_release_evidence(
        DEFAULT_BH_KNOWLEDGE.source_contract,
        DEFAULT_BH_KNOWLEDGE.dialect,
        DEFAULT_BH_KNOWLEDGE.ontology_version,
    )
    return {
        "verified_release_profile_ids": list(
            release_evidence_module.trusted_release_profile_ids()
        ),
        "release_profile_verified": evidence is not None,
        "release_evidence": evidence.to_dict() if evidence is not None else None,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_corpus_digest(paths: list[Path]) -> str:
    return _canonical_digest(
        [
            {"name": path.name, "sha256": _sha256_file(path)}
            for path in sorted(paths, key=lambda item: item.name)
        ]
    )


def _portable_manual_comparison(
    result: dict[str, Any],
    *,
    manual_name: str,
) -> dict[str, Any]:
    """Remove machine-local paths from an otherwise stable audit payload."""

    portable = json.loads(json.dumps(result, ensure_ascii=False))
    values = portable.get("values")
    if isinstance(values, dict) and "manual_reference" in values:
        values["manual_reference"] = manual_name
    return portable


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _is_within(path: Path | None, parent: Path) -> bool:
    if path is None:
        return False
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _integrated_route_once(source_path: Path, output_dir: Path) -> dict[str, Any]:
    """Exercise the real writer/router without retaining machine-local paths."""

    production_path, review_path, report_path, report = split_bh_dxf(
        source_path,
        output_dir,
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
    )
    route = str(report.get("automation_route") or "unknown")
    outputs = report.get("outputs", {}) or {}
    disposition = str(
        (report.get("proof_report", {}) or {}).get("disposition")
        or (report.get("automation_assessment", {}) or {}).get("disposition")
        or route
    )
    written_path = production_path or review_path
    output_sha256 = (
        _sha256_file(written_path)
        if written_path is not None and written_path.exists()
        else None
    )
    base = source_path.name.removesuffix("_拆板前.dxf")
    production_root = output_dir / "auto_accepted"
    review_root = output_dir / "review_required" / base
    rejected_root = output_dir / "unprocessable" / base
    root_dxf = sorted(production_root.glob("*.dxf"))
    quarantine_clean = sorted(
        path
        for root in (output_dir / "review_required", output_dir / "unprocessable")
        if root.exists()
        for path in root.rglob("*清洁*.dxf")
    )
    source_copy = Path(str(outputs["source_copy"])) if outputs.get("source_copy") else None
    preview_root = production_root if route == "production" else review_root
    preview_pair_valid = (
        validate_preview_pair(outputs.get("previews"), root=preview_root)
        if route in {"production", "review_required"}
        else None
    )
    if route == "production":
        routing_valid = bool(
            disposition == "auto_accept"
            and production_path is not None
            and production_path.exists()
            and production_path.parent.resolve() == production_root.resolve()
            and review_path is None
            and root_dxf == [production_path]
            and outputs.get("production_clean")
            and outputs.get("review_candidate") is None
            and outputs.get("source_copy") is None
            and preview_pair_valid is True
            and not quarantine_clean
        )
    elif route == "review_required":
        routing_valid = bool(
            disposition == "review_required"
            and production_path is None
            and review_path is not None
            and review_path.exists()
            and _is_within(review_path, review_root)
            and _is_within(report_path, review_root)
            and _is_within(source_copy, review_root)
            and source_copy is not None
            and source_copy.exists()
            and not root_dxf
            and outputs.get("production_clean") is None
            and outputs.get("review_candidate")
            and preview_pair_valid is True
            and not quarantine_clean
        )
    elif route == "rejected":
        routing_valid = bool(
            disposition == "rejected"
            and production_path is None
            and review_path is None
            and _is_within(report_path, rejected_root)
            and _is_within(source_copy, rejected_root)
            and source_copy is not None
            and source_copy.exists()
            and not root_dxf
            and outputs.get("production_clean") is None
            and outputs.get("review_candidate") is None
            and not quarantine_clean
        )
    else:
        routing_valid = False
    saved = report.get("saved_dxf")
    saved_dxf_ok = (
        bool(saved.get("ok"))
        if isinstance(saved, dict)
        else None if route == "rejected" else False
    )
    return {
        "route": route,
        "proof_disposition": disposition,
        "output_kind": (
            "production_clean"
            if production_path is not None
            else "review_candidate"
            if review_path is not None
            else "none"
        ),
        "output_sha256": output_sha256,
        "output_size_bytes": (
            written_path.stat().st_size
            if written_path is not None and written_path.exists()
            else None
        ),
        "saved_dxf_ok": saved_dxf_ok,
        "physical_routing_valid": routing_valid,
        "preview_pair_valid": preview_pair_valid,
        "production_root_dxf_count": len(root_dxf),
        "quarantine_clean_dxf_count": len(quarantine_clean),
    }


def _verify_integrated_writer(
    source_path: Path,
    repeat_count: int,
) -> dict[str, Any]:
    """Repeat the integrated save path and compare emitted DXF bytes."""

    with TemporaryDirectory(prefix="bh-release-writer-") as temporary:
        root = Path(temporary)
        runs = [
            _integrated_route_once(source_path, root / f"run-{index:02d}")
            for index in range(repeat_count + 1)
        ]
    routes = {str(item.get("route")) for item in runs}
    hashes = [item.get("output_sha256") for item in runs]
    no_output_expected = routes == {"rejected"}
    output_bytes_deterministic = bool(runs) and (
        all(value is None for value in hashes)
        if no_output_expected
        else all(isinstance(value, str) and len(value) == 64 for value in hashes)
        and len(set(hashes)) == 1
    )
    return {
        "run_count": len(runs),
        "runs": runs,
        "route_deterministic": len(routes) == 1,
        "output_bytes_deterministic": output_bytes_deterministic,
        "physical_routing_valid": all(
            item.get("physical_routing_valid") is True for item in runs
        ),
        "preview_pair_valid": all(
            item.get("preview_pair_valid") is True
            for item in runs
            if item.get("route") in {"production", "review_required"}
        ),
        "saved_dxf_valid": all(
            item.get("saved_dxf_ok") is True
            or (
                item.get("route") == "rejected"
                and item.get("saved_dxf_ok") is None
            )
            for item in runs
        ),
        "output_sha256": hashes[0] if hashes else None,
    }


def _clone_document(doc: Drawing) -> Drawing:
    stream = StringIO()
    doc.write(stream)
    stream.seek(0)
    return ezdxf.read(stream)


def _transform(doc: Drawing, matrix: Matrix44) -> Drawing:
    mutated = _clone_document(doc)
    for entity in list(mutated.modelspace()):
        entity.transform(matrix)
    return mutated


def _uppercase_layers(doc: Drawing) -> Drawing:
    mutated = _clone_document(doc)
    for entity in mutated.entitydb.values():
        if entity.is_alive and entity.dxf.is_supported("layer"):
            entity.dxf.layer = str(entity.dxf.layer).upper()
    return mutated


def _explode(doc: Drawing) -> Drawing:
    mutated = _clone_document(doc)
    modelspace = mutated.modelspace()
    for insert in list(modelspace.query("INSERT")):
        insert.explode(target_layout=modelspace)
    return mutated


def _mutation(name: str) -> Callable[[Drawing], Drawing]:
    functions: dict[str, Callable[[Drawing], Drawing]] = {
        "translate": lambda doc: _transform(
            doc,
            Matrix44.translate(1234.5, -678.25, 0.0),
        ),
        "mirror_x": lambda doc: _transform(
            doc,
            Matrix44.scale(-1.0, 1.0, 1.0),
        ),
        "mirror_y": lambda doc: _transform(
            doc,
            Matrix44.scale(1.0, -1.0, 1.0),
        ),
        "uppercase_layers": _uppercase_layers,
        "explode": _explode,
        "rotate90": lambda doc: _transform(
            doc,
            Matrix44.z_rotate(radians(90.0)),
        ),
        "rotate37": lambda doc: _transform(
            doc,
            Matrix44.z_rotate(radians(37.0)),
        ),
    }
    return functions[name]


def _diagnostic_codes(result: BHCompileResult) -> list[str]:
    return sorted(
        {
            item.diagnostic_code
            for item in result.proof_report.obligations
            if item.diagnostic_code is not None
        }
    )


def _information_semantic_snapshot(result: BHCompileResult) -> dict[str, Any]:
    ledger = result.information_ledger
    return {
        "source_entity_count": ledger["source_entity_count"],
        "inventory_by_semantic_role": ledger["inventory_by_semantic_role"],
        "inventory_by_entity_type": ledger["inventory_by_entity_type"],
        "inventory_by_visibility": ledger["inventory_by_visibility"],
        "semantic_object_counts": ledger["semantic_object_counts"],
        "semantic_relation_counts": ledger["semantic_relation_counts"],
        "policy": ledger["policy"],
    }


def _snapshot(result: BHCompileResult) -> dict[str, Any]:
    information_snapshot = _information_semantic_snapshot(result)
    knowledge = result.assembly.diagnostics["knowledge_base"]
    return {
        "compiler_version": __version__,
        "source_contract": knowledge["source_contract"],
        "source_contract_enforcement": {
            "authority": "workflow_supplied_not_inferred_from_dxf",
            "validated_before_source_ir": True,
            "dialect_profile_must_match_contract": True,
            **_release_evidence_snapshot(),
        },
        "fingerprint_algorithm": result.fingerprints["algorithm"],
        "source_fact_fingerprint": result.fingerprints["source_fact_ir"],
        "selected_hypothesis_fingerprint": result.fingerprints[
            "selected_hypothesis"
        ],
        "manufacturing_fingerprint": result.manufacturing_ir.fingerprint,
        "source_information_semantic_digest": _canonical_digest(
            information_snapshot
        ),
        "source_information_ledger": result.information_ledger,
        "writer_assembly_fingerprint": result.fingerprints["writer_assembly"],
        "proof_disposition": result.proof_report.disposition.value,
        "blocking_obligation_ids": list(
            result.proof_report.blocking_obligation_ids
        ),
        "diagnostic_codes": _diagnostic_codes(result),
        "proof_report": result.proof_report.to_dict(),
        "search_status": {
            "search_complete": result.hypotheses.search_complete,
            "generated_candidate_count": result.hypotheses.generated_candidate_count,
            "evaluated_candidate_count": result.hypotheses.evaluated_candidate_count,
            "pruned_candidate_count": result.hypotheses.pruned_candidate_count,
            "termination_reason": result.hypotheses.termination_reason,
        },
        "manufacturing_validation": result.manufacturing_validation.to_dict(),
        "physical_plates": [
            {
                "role": plate.role.value,
                "material": plate.material,
                "thickness_mm": plate.thickness_mm,
                "quantity": plate.quantity,
                "outer_segment_count": len(plate.outer_segments),
                "circular_cut_count": len(plate.circular_cuts),
                "inner_contour_count": len(plate.inner_contours),
            }
            for plate in result.manufacturing_ir.plates
        ],
    }


def _rejected_snapshot(error: BHCompilationRejected) -> dict[str, Any]:
    return {
        "status": "rejected",
        "proof_disposition": error.proof_report.disposition.value,
        "blocking_obligation_ids": list(error.proof_report.blocking_obligation_ids),
        "diagnostic_codes": list(error.diagnostic_codes),
        "proof_report": error.proof_report.to_dict(),
        "manufacturing_fingerprint": (
            error.manufacturing_ir.fingerprint
            if error.manufacturing_ir is not None
            else None
        ),
    }


def _compile_outcome(
    doc: Drawing,
    *,
    source_path: Path,
    diagnostic_rotation: bool = False,
) -> tuple[dict[str, Any], BHCompileResult | None]:
    knowledge = (
        replace(DEFAULT_BH_KNOWLEDGE, horizontal_axis_fact=False)
        if diagnostic_rotation
        else DEFAULT_BH_KNOWLEDGE
    )
    try:
        result = BHCompiler(knowledge=knowledge).compile(
            doc,
            source_contract=knowledge.source_contract,
            source_path=source_path,
        )
    except BHCompilationRejected as error:
        return _rejected_snapshot(error), None
    except ValueError as error:
        return {
            "status": "error",
            "error_type": type(error).__name__,
            "message": str(error),
        }, None
    snapshot = _snapshot(result)
    return {"status": "compiled", "snapshot": snapshot}, result


def _mutation_record(
    source_path: Path,
    name: str,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    mutated = _mutation(name)(load_document(source_path))
    production, _ = _compile_outcome(mutated, source_path=source_path)
    record: dict[str, Any] = {
        "name": name,
        "contract": (
            "strict_representation_invariant"
            if name in STRICT_MUTATIONS
            else "diagnostic_only"
        ),
        "production_compile": production,
    }
    if production["status"] == "compiled":
        snapshot = production["snapshot"]
        record["manufacturing_fingerprint_equal"] = (
            snapshot["manufacturing_fingerprint"]
            == baseline["manufacturing_fingerprint"]
        )
        record["proof_disposition_equal"] = (
            snapshot["proof_disposition"] == baseline["proof_disposition"]
        )
        record["source_information_semantics_equal"] = (
            snapshot["source_information_semantic_digest"]
            == baseline["source_information_semantic_digest"]
        )
    else:
        record["manufacturing_fingerprint_equal"] = False
        record["proof_disposition_equal"] = False
        record["source_information_semantics_equal"] = False
    if name in DIAGNOSTIC_MUTATIONS:
        diagnostic_doc = _mutation(name)(load_document(source_path))
        diagnostic, _ = _compile_outcome(
            diagnostic_doc,
            source_path=source_path,
            diagnostic_rotation=True,
        )
        record["diagnostic_compile"] = diagnostic
        if diagnostic["status"] == "compiled":
            record["diagnostic_manufacturing_fingerprint_equal"] = (
                diagnostic["snapshot"]["manufacturing_fingerprint"]
                == baseline["manufacturing_fingerprint"]
            )
        else:
            record["diagnostic_manufacturing_fingerprint_equal"] = False
    del mutated
    gc.collect()
    return record


def _verify_sample(
    source_path: Path,
    *,
    corpus_case: CorpusCase,
    reference_dir: Path,
    mutations: tuple[str, ...],
    repeat_count: int,
    continue_on_failure: bool,
) -> dict[str, Any]:
    stem = source_path.name.removesuffix("_拆板前.dxf")
    manual_path = corpus_case.manual_path(reference_dir)
    source_sha256 = _sha256_file(source_path)

    original_outcome, compiled = _compile_outcome(
        load_document(source_path),
        source_path=source_path,
    )
    if compiled is None:
        if not continue_on_failure:
            raise RuntimeError(
                f"Original compilation failed for {source_path.name}: {original_outcome}"
            )
        return {
            "schema": "BH-RELEASE-SAMPLE-VERIFICATION-1.0",
            "stem": stem,
            "source": {
                "name": source_path.name,
                "sha256": source_sha256,
            },
            "manual": None,
            "production_baseline": original_outcome,
            "baseline_compiled": False,
            "repeat_runs": [],
            "repeat_deterministic": False,
            "mutations": [],
            "strict_mutations_equivalent": False,
            "integrated_writer_verification": None,
            "post_hoc_supervision": None,
        }

    baseline = original_outcome["snapshot"]
    repeat_runs = []
    for index in range(repeat_count):
        outcome, _ = _compile_outcome(
            load_document(source_path),
            source_path=source_path,
        )
        repeat_runs.append(
            {
                "repeat_index": index + 1,
                "outcome": outcome,
                "snapshot_digest": (
                    _canonical_digest(outcome["snapshot"])
                    if outcome["status"] == "compiled"
                    else None
                ),
            }
        )
        gc.collect()
    baseline_digest = _canonical_digest(baseline)
    repeat_deterministic = all(
        item["outcome"]["status"] == "compiled"
        and item["snapshot_digest"] == baseline_digest
        for item in repeat_runs
    )

    mutation_records = [
        _mutation_record(source_path, name, baseline)
        for name in mutations
    ]
    strict_records = [
        item
        for item in mutation_records
        if item["contract"] == "strict_representation_invariant"
    ]
    strict_mutations_equivalent = all(
        item["production_compile"]["status"] == "compiled"
        and item["manufacturing_fingerprint_equal"]
        and item["proof_disposition_equal"]
        and item["source_information_semantics_equal"]
        for item in strict_records
    )

    integrated_writer = _verify_integrated_writer(
        source_path,
        repeat_count,
    )

    # Manual bytes and their manifest hash remain unread until the source-only
    # baseline, repeats, mutations and integrated writer route are frozen.
    validate_corpus_manual_file(corpus_case, reference_dir)
    manual_sha256 = _sha256_file(manual_path) if manual_path.exists() else None
    comparison = None
    if manual_path.exists():
        comparison = _portable_manual_comparison(
            compare_bh_to_manual(compiled.assembly, manual_path).to_dict(),
            manual_name=manual_path.name,
        )
    sample = {
        "schema": "BH-RELEASE-SAMPLE-VERIFICATION-1.0",
        "stem": stem,
        "source": {
            "name": source_path.name,
            "sha256": source_sha256,
        },
        "manual": (
            {"name": manual_path.name, "sha256": manual_sha256}
            if manual_sha256 is not None
            else None
        ),
        "production_baseline": {
            **original_outcome,
            "decision_input_channels": [
                "source_dxf_facts",
                "canonical_member_frame",
                "drawing_associations",
                "bh_engineering_constraints",
                "proof_obligations",
            ],
            "manual_reference": None,
        },
        "baseline_compiled": True,
        "repeat_runs": repeat_runs,
        "repeat_deterministic": repeat_deterministic,
        "mutations": mutation_records,
        "strict_mutations_equivalent": strict_mutations_equivalent,
        "integrated_writer_verification": integrated_writer,
        "post_hoc_supervision": {
            "used_for_decision": False,
            "manual_read_after_baseline_frozen": True,
            "verification_gate_applicable": (
                baseline["proof_disposition"] == "auto_accept"
            ),
            "gate_policy": "auto_accept_only",
            "comparison": comparison,
        },
    }
    del compiled
    gc.collect()
    return sample


def _capability_matrix(
    *,
    pair_count: int,
    mutations: tuple[str, ...],
    all_strict_mutations_equivalent: bool,
) -> dict[str, Any]:
    strict = [name for name in mutations if name in STRICT_MUTATIONS]
    diagnostic = [name for name in mutations if name in DIAGNOSTIC_MUTATIONS]
    return {
        "schema": "BH-RELEASE-CAPABILITY-MATRIX-1.0",
        "compiler_version": __version__,
        "source_contract": {
            **asdict(DEFAULT_BH_KNOWLEDGE.source_contract),
            "authority": "workflow_supplied_not_inferred_from_dxf",
            "explicit_authorization_required": True,
            "runtime_enforced": True,
            "dialect_profile_must_match_contract": True,
            **_release_evidence_snapshot(),
        },
        "ground_truth_firewall": {
            "used_for_decision": False,
            "allowed_phase": "post_hoc_after_source_only_results_are_frozen",
        },
        "verified_current_run": {
            "tekla_bh_pair_count": pair_count,
            "horizontal_member_x_axis": True,
            "strict_representation_mutations": strict,
            "all_strict_mutations_equivalent": all_strict_mutations_equivalent,
            "manufacturing_scope": (
                "net plate outer contours, circular cuts, shaped inner openings, "
                "material, thickness, physical role and quantity"
            ),
        },
        "review_only": {
            "missing_critical_independent_evidence": (
                "candidate and source are quarantined for engineering review"
            ),
            "equivalent_or_unresolved_semantic_ambiguity": (
                "never promoted by score margin alone"
            ),
        },
        "diagnostic_only": {
            "arbitrary_rotation": (
                diagnostic
                or ["rotate90", "rotate37"]
            ),
            "production_contract": "member longitudinal X axis is horizontal",
        },
        "unsupported_or_unverified": {
            "multi_member_drawings": "not verified for unattended production",
            "other_dxf_exporters": "only the current Tekla dialect family is verified",
            "inferred_bevel_weld_kerf": "not emitted by the net-geometry compiler",
            "dstv_nc_output": "not implemented",
            "non_bh_profiles": "outside this compiler capability claim",
        },
    }


def _summary_markdown(summary: dict[str, Any]) -> str:
    counts = summary["disposition_counts"]
    lines = [
        f"# BH Semantic Compiler v{__version__} Verification",
        "",
        f"- Pair count: {summary['pair_count']}",
        f"- Auto accepted: {counts['auto_accept']}",
        f"- Review required: {counts['review_required']}",
        f"- Rejected/error: {counts['rejected_or_error']}",
        f"- All post-hoc comparisons passed: {summary['all_original_supervised_ok']}",
        f"- Auto-accepted post-hoc gate passed: {summary['all_auto_accepted_supervised_ok']}",
        f"- Repeat deterministic: {summary['all_repeat_deterministic']}",
        f"- Strict mutations equivalent: {summary['all_strict_mutations_equivalent']}",
        f"- Frozen engineering routes match: {summary['all_expected_routes_match']}",
        f"- Overall verification passed: {summary['all_passed']}",
        "",
        "Manual split drawings were read only after source-only baselines, repeats and mutations were frozen.",
        "",
        "| Sample | Disposition | Repeat | Strict mutations | Supervision |",
        "|---|---|---:|---:|---:|",
    ]
    for item in summary["samples"]:
        lines.append(
            f"| {item['stem']} | {item['proof_disposition']} | "
            f"{item['repeat_deterministic']} | "
            f"{item['strict_mutations_equivalent']} | "
            f"{item['supervised_ok']} |"
        )
    return "\n".join(lines) + "\n"


def _build_verification(
    source_dir: Path,
    reference_dir: Path,
    output_dir: Path,
    *,
    mutations: tuple[str, ...],
    repeat_count: int,
    continue_on_failure: bool,
    release_profile: bool = True,
    manifest_path: Path = DEFAULT_CORPUS_MANIFEST,
    release_trust_mode: str = "current_code_pinned",
) -> dict[str, Any]:
    source_dir = Path(source_dir)
    reference_dir = Path(reference_dir)
    output_dir = Path(output_dir)
    if repeat_count < 1:
        raise ValueError("repeat_count must be at least 1")
    unknown = sorted(set(mutations) - KNOWN_MUTATIONS)
    if unknown:
        raise ValueError("Unknown mutations: " + ", ".join(unknown))
    if len(set(mutations)) != len(mutations):
        raise ValueError("Mutation names must be unique.")
    missing_release_mutations = sorted(
        REQUIRED_RELEASE_MUTATIONS - set(mutations)
    )
    if release_profile and missing_release_mutations:
        raise ValueError(
            "Missing required release mutations: "
            + ", ".join(missing_release_mutations)
        )
    manifest = load_corpus_manifest(Path(manifest_path))
    discovered = sorted(source_dir.glob("*_拆板前.dxf"))
    if not discovered:
        raise ValueError(f"No *_拆板前.dxf sources found in {source_dir}")
    cases_by_source = {case.source_file: case for case in manifest.cases}
    unknown_sources = [path.name for path in discovered if path.name not in cases_by_source]
    if unknown_sources:
        raise ValueError(
            "Sources are not declared by the corpus manifest: "
            + ", ".join(unknown_sources)
        )
    selected_cases = tuple(cases_by_source[path.name] for path in discovered)
    for case in selected_cases:
        # Source integrity is checked before compilation. Manual integrity is
        # intentionally deferred inside _verify_sample until the decision and
        # writer route have been frozen.
        validate_corpus_source_file(case, source_dir)
    sources = [case.source_path(source_dir) for case in selected_cases]
    release_evidence = release_evidence_module.resolve_release_evidence(
        DEFAULT_BH_KNOWLEDGE.source_contract,
        DEFAULT_BH_KNOWLEDGE.dialect,
        DEFAULT_BH_KNOWLEDGE.ontology_version,
    )
    current_source_corpus_digest = _source_corpus_digest(sources)
    release_evidence_corpus_match = bool(
        release_evidence is not None
        and release_evidence.source_count == len(sources)
        and release_evidence.source_corpus_sha256 == current_source_corpus_digest
    )

    samples_dir = output_dir / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    for stale in samples_dir.glob("*.json"):
        stale.unlink()
    sample_reports = []
    for case, source in zip(selected_cases, sources):
        sample = _verify_sample(
            source,
            corpus_case=case,
            reference_dir=reference_dir,
            mutations=mutations,
            repeat_count=repeat_count,
            continue_on_failure=continue_on_failure,
        )
        sample_reports.append(sample)
        _write_json(samples_dir / f"{sample['stem']}.json", sample)

    summary_samples = []
    for case, sample in zip(selected_cases, sample_reports):
        baseline = sample.get("production_baseline", {}) or {}
        snapshot = baseline.get("snapshot", {}) or {}
        supervision = sample.get("post_hoc_supervision", {}) or {}
        comparison = supervision.get("comparison", {}) or {}
        writer = sample.get("integrated_writer_verification", {}) or {}
        actual_disposition = snapshot.get(
            "proof_disposition",
            baseline.get("proof_disposition", "rejected"),
        )
        actual_blockers = snapshot.get(
            "blocking_obligation_ids",
            baseline.get("blocking_obligation_ids", []),
        )
        route_matches_manifest = bool(
            actual_disposition == case.disposition
            and actual_blockers == list(case.blocking_proof_ids)
        )
        summary_samples.append(
            {
                "stem": sample["stem"],
                "report_path": f"samples/{sample['stem']}.json",
                "source_sha256": sample["source"]["sha256"],
                "manual_sha256": (
                    sample["manual"]["sha256"]
                    if sample.get("manual") is not None
                    else None
                ),
                "manufacturing_fingerprint": snapshot.get(
                    "manufacturing_fingerprint"
                ),
                "proof_disposition": actual_disposition,
                "blocking_obligation_ids": actual_blockers,
                "expected_proof_disposition": case.disposition,
                "expected_blocking_obligation_ids": list(
                    case.blocking_proof_ids
                ),
                "route_matches_manifest": route_matches_manifest,
                "diagnostic_codes": snapshot.get(
                    "diagnostic_codes",
                    baseline.get("diagnostic_codes", []),
                ),
                "repeat_deterministic": sample["repeat_deterministic"],
                "strict_mutations_equivalent": sample[
                    "strict_mutations_equivalent"
                ],
                "writer_output_bytes_deterministic": writer.get(
                    "output_bytes_deterministic",
                    False,
                ),
                "physical_routing_valid": writer.get(
                    "physical_routing_valid",
                    False,
                ),
                "writer_output_sha256": writer.get("output_sha256"),
                "supervised_ok": comparison.get("ok", False),
                "supervision_gate_applicable": snapshot.get(
                    "proof_disposition",
                    baseline.get("proof_disposition", "rejected"),
                )
                == "auto_accept",
                "supervision_metrics": comparison.get("values"),
            }
        )

    all_originals_compiled = all(
        sample.get("baseline_compiled") is True for sample in sample_reports
    )
    all_original_supervised_ok = all(
        item["supervised_ok"] is True for item in summary_samples
    )
    auto_gate_items = [
        item for item in summary_samples if item["supervision_gate_applicable"]
    ]
    all_auto_accepted_supervised_ok: bool | None = (
        all(item["supervised_ok"] is True for item in auto_gate_items)
        if auto_gate_items
        else None
    )
    auto_supervision_gate_status = (
        "not_applicable"
        if all_auto_accepted_supervised_ok is None
        else "passed"
        if all_auto_accepted_supervised_ok
        else "failed"
    )
    all_repeat_deterministic = all(
        item["repeat_deterministic"] is True for item in summary_samples
    )
    all_strict_mutations_equivalent = all(
        item["strict_mutations_equivalent"] is True for item in summary_samples
    )
    all_writer_outputs_deterministic = all(
        item["writer_output_bytes_deterministic"] is True
        for item in summary_samples
    )
    all_physical_routing_valid = all(
        item["physical_routing_valid"] is True for item in summary_samples
    )
    all_expected_routes_match = all(
        item["route_matches_manifest"] is True for item in summary_samples
    )
    required_release_mutations_present = not missing_release_mutations
    dispositions = [item["proof_disposition"] for item in summary_samples]
    disposition_counts = {
        "auto_accept": dispositions.count("auto_accept"),
        "review_required": dispositions.count("review_required"),
        "rejected_or_error": sum(
            item not in {"auto_accept", "review_required"}
            for item in dispositions
        ),
    }
    summary = {
        "schema": "BH-RELEASE-VERIFICATION-1.0",
        "corpus_manifest_schema": manifest.schema_version,
        "corpus_manifest": Path(manifest_path).name,
        "compiler_version": __version__,
        "verification_policy": {
            "profile": "release" if release_profile else "reduced_non_release",
            "release_trust_mode": release_trust_mode,
            "current_source_corpus_sha256": current_source_corpus_digest,
            "release_evidence_corpus_match": release_evidence_corpus_match,
            "production_member_axis": "horizontal_x",
            "source_contract": {
                **asdict(DEFAULT_BH_KNOWLEDGE.source_contract),
                "authority": "workflow_supplied_not_inferred_from_dxf",
                "explicit_authorization_required": True,
                "runtime_enforced": True,
                "dialect_profile_must_match_contract": True,
                **_release_evidence_snapshot(),
            },
            "ground_truth_used_for_decision": False,
            "post_hoc_supervision_gate": "auto_accept_only",
            "review_mismatch_effect": "record_only_already_quarantined",
            "manual_read_phase": (
                "after_baseline_repeats_mutations_and_writer_routes_frozen"
            ),
            "required_release_mutations": sorted(REQUIRED_RELEASE_MUTATIONS),
            "missing_release_mutations": missing_release_mutations,
            "strict_mutations": [
                name for name in mutations if name in STRICT_MUTATIONS
            ],
            "diagnostic_mutations": [
                name for name in mutations if name in DIAGNOSTIC_MUTATIONS
            ],
        },
        "pair_count": len(sample_reports),
        "repeat_count": repeat_count,
        "mutations": list(mutations),
        "disposition_counts": disposition_counts,
        "all_originals_compiled": all_originals_compiled,
        "all_original_supervised_ok": all_original_supervised_ok,
        "all_auto_accepted_supervised_ok": all_auto_accepted_supervised_ok,
        "auto_supervision_gate_status": auto_supervision_gate_status,
        "all_repeat_deterministic": all_repeat_deterministic,
        "all_strict_mutations_equivalent": all_strict_mutations_equivalent,
        "all_writer_outputs_deterministic": all_writer_outputs_deterministic,
        "all_physical_routing_valid": all_physical_routing_valid,
        "all_expected_routes_match": all_expected_routes_match,
        "required_release_mutations_present": required_release_mutations_present,
        "all_passed": (
            release_profile
            and required_release_mutations_present
            and all_originals_compiled
            and all_auto_accepted_supervised_ok is True
            and all_repeat_deterministic
            and all_strict_mutations_equivalent
            and all_writer_outputs_deterministic
            and all_physical_routing_valid
            and all_expected_routes_match
            and disposition_counts["rejected_or_error"] == 0
        ),
        "samples": summary_samples,
    }
    capabilities = _capability_matrix(
        pair_count=len(sample_reports),
        mutations=mutations,
        all_strict_mutations_equivalent=all_strict_mutations_equivalent,
    )
    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "capability_matrix.json", capabilities)
    (output_dir / "summary.md").write_text(
        _summary_markdown(summary),
        encoding="utf-8",
        newline="\n",
    )
    return summary


def build_verification(
    source_dir: Path,
    reference_dir: Path,
    output_dir: Path,
    *,
    mutations: tuple[str, ...],
    repeat_count: int,
    continue_on_failure: bool,
    release_profile: bool = True,
    manifest_path: Path = DEFAULT_CORPUS_MANIFEST,
    candidate_from_prior_release: bool = False,
) -> dict[str, Any]:
    """Build normal evidence, or an isolated candidate using prior source trust.

    Candidate mode patches only this verification process.  It reuses a
    code-pinned prior artifact after validating its source contract, dialect,
    declared ontology transition and corpus bindings, while the current
    compiler re-proves every geometry and output property.  Production
    compilation remains fail-closed.
    """

    if not candidate_from_prior_release:
        return _build_verification(
            source_dir,
            reference_dir,
            output_dir,
            mutations=mutations,
            repeat_count=repeat_count,
            continue_on_failure=continue_on_failure,
            release_profile=release_profile,
            manifest_path=manifest_path,
        )
    prior = release_evidence_module.resolve_prior_release_evidence_for_candidate(
        DEFAULT_BH_KNOWLEDGE.source_contract,
        DEFAULT_BH_KNOWLEDGE.dialect,
        DEFAULT_BH_KNOWLEDGE.ontology_version,
    )
    if prior is None:
        raise ValueError(
            "Candidate verification requires a valid code-pinned prior-version "
            "release artifact for the same source contract and dialect."
        )
    with patch.object(
        release_evidence_module,
        "resolve_release_evidence",
        release_evidence_module.resolve_prior_release_evidence_for_candidate,
    ):
        return _build_verification(
            source_dir,
            reference_dir,
            output_dir,
            mutations=mutations,
            repeat_count=repeat_count,
            continue_on_failure=continue_on_failure,
            release_profile=release_profile,
            manifest_path=manifest_path,
            release_trust_mode="prior_version_candidate",
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a source-first, ground-truth-firewalled BH release verification artifact."
        )
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        required=True,
        help="Directory containing only *_拆板前.dxf source drawings.",
    )
    parser.add_argument(
        "--reference-dir",
        type=Path,
        required=True,
        help=(
            "Offline directory containing *_拆板后.dxf manual references; "
            "read only after source-only decisions and writer routes are frozen."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_CORPUS_MANIFEST,
        help="Offline corpus manifest; manual hashes are checked only post-hoc.",
    )
    parser.add_argument(
        "--mutations",
        default="translate,mirror_x,mirror_y,uppercase_layers,explode",
        help="Comma-separated representation mutations.",
    )
    parser.add_argument("--repeat-count", type=int, default=2)
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument(
        "--allow-reduced-profile",
        action="store_true",
        help=(
            "Allow omitting required release mutations for diagnostics; "
            "the resulting artifact is explicitly non-release and all_passed is false."
        ),
    )
    parser.add_argument(
        "--candidate-from-prior-release",
        action="store_true",
        help=(
            "Offline-only bootstrap: retain the pinned prior profile/corpus trust "
            "while re-verifying all compiler and writer capabilities."
        ),
    )
    parser.add_argument(
        "--emit-release-evidence",
        type=Path,
        help=(
            "Write a new code-pinnable evidence JSON only when the complete "
            "20-source prior-release candidate gate passes."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    mutations = tuple(
        item.strip()
        for item in args.mutations.split(",")
        if item.strip()
    )
    summary = build_verification(
        args.source_dir,
        args.reference_dir,
        args.output_dir,
        mutations=mutations,
        repeat_count=args.repeat_count,
        continue_on_failure=args.continue_on_failure,
        release_profile=not args.allow_reduced_profile,
        manifest_path=args.manifest,
        candidate_from_prior_release=args.candidate_from_prior_release,
    )
    if args.emit_release_evidence is not None:
        payload = build_release_capability_payload(
            summary,
            contract=DEFAULT_BH_KNOWLEDGE.source_contract,
            dialect=DEFAULT_BH_KNOWLEDGE.dialect,
            ontology_version=DEFAULT_BH_KNOWLEDGE.ontology_version,
        )
        _write_json(args.emit_release_evidence, payload)
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "pair_count",
                    "all_originals_compiled",
                    "all_original_supervised_ok",
                    "all_auto_accepted_supervised_ok",
                    "all_repeat_deterministic",
                    "all_strict_mutations_equivalent",
                    "all_writer_outputs_deterministic",
                    "all_physical_routing_valid",
                    "all_expected_routes_match",
                    "all_passed",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
