"""External-evidence runner for BH/BOX production-standard acceptance."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any

from steel_dxf_split.bh_knowledge import BHSourceContract
from steel_dxf_split.bh_pipeline import split_bh_dxf
from steel_dxf_split.box.compiler import compile_box_core
from steel_dxf_split.box.contracts import BoxSourceContract
from steel_dxf_split.box.equivalence import group_equivalent_plate_pairs
from steel_dxf_split.box.validator import validate_saved_dxf
from steel_dxf_split.box.writer import OutputPurpose, write_box_clean

from .constraints import ConstraintEvaluation, evaluate_human_constraints
from .contracts import (
    ConstraintResult,
    ConstraintStatus,
    EvidenceLevel,
    FinalStatus,
    SampleContract,
    classify_verdict,
)
from .corpus import build_sample_contracts, source_snapshot
from .geometry import WholeDrawingComparison, compare_groups_to_reference
from .manual_reference import load_snapshot_reference


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _within_root(root: Path, relative_path: str) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved = (resolved_root / relative_path).resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"evidence path escapes sample root: {relative_path}") from error
    return resolved


@dataclass(frozen=True, slots=True)
class RunnerHooks:
    compile_source: Callable[[Path], Any]
    group_outputs: Callable[[Any], tuple[Any, ...]]
    write_candidate: Callable[[Any, Path, OutputPurpose], Any]
    validate_candidate: Callable[[Path, Any, Any], dict[str, object]]
    load_reference: Callable[[Path, str, str], Any]
    compare_reference: Callable[
        [tuple[Any, ...], Any, str, str], WholeDrawingComparison
    ]
    evaluate_constraints: Callable[
        [SampleContract, tuple[Any, ...], bool], ConstraintEvaluation
    ]
    compile_bh_source: Callable[[Path, Path], Any] | None = None


@dataclass(frozen=True, slots=True)
class BHCompiledCandidate:
    internal_disposition: str
    candidate_path: Path
    candidate_validation: dict[str, object]
    manufacturing_fingerprint: str | None
    profile: str | None
    nominal_length_mm: float | None
    hypothesis_count: int | None


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _compile_bh_candidate(source: Path, candidate_root: Path) -> BHCompiledCandidate:
    """Run the existing BH core and expose only acceptance-level fields."""

    production, review, _report_path, report = split_bh_dxf(
        source,
        candidate_root / source.stem,
        source_contract=BHSourceContract(),
        require_auto_accept=False,
    )
    candidate = production or review
    proof = _mapping(report.get("proof_report"))
    disposition = proof.get("disposition")
    if candidate is None or not isinstance(disposition, str):
        error = _mapping(report.get("compilation_error"))
        detail = error.get("message")
        raise RuntimeError(
            "BH core did not produce a materializable candidate"
            + (f": {detail}" if isinstance(detail, str) and detail else "")
        )
    saved = _mapping(report.get("saved_dxf"))
    if saved.get("ok") is not True:
        raise RuntimeError("BH candidate DXF failed writer/reopen validation")
    manufacturing = _mapping(report.get("manufacturing_ir"))
    search = _mapping(report.get("search_status"))
    hypothesis_count = search.get("generated_candidate_count")
    return BHCompiledCandidate(
        internal_disposition=disposition,
        candidate_path=candidate,
        candidate_validation=saved,
        manufacturing_fingerprint=(
            str(manufacturing["fingerprint"])
            if isinstance(manufacturing.get("fingerprint"), str)
            else None
        ),
        profile=(
            str(manufacturing["profile"])
            if isinstance(manufacturing.get("profile"), str)
            else None
        ),
        nominal_length_mm=(
            float(manufacturing["nominal_length_mm"])
            if isinstance(manufacturing.get("nominal_length_mm"), int | float)
            else None
        ),
        hypothesis_count=(
            int(hypothesis_count) if isinstance(hypothesis_count, int) else None
        ),
    )


def _default_hooks() -> RunnerHooks:
    return RunnerHooks(
        compile_source=lambda path: compile_box_core(path, BoxSourceContract()),
        group_outputs=lambda plates: group_equivalent_plate_pairs(plates),
        write_candidate=lambda manufacturing, path, purpose: write_box_clean(
            manufacturing,
            path,
            purpose=purpose,
        ),
        validate_candidate=lambda path, manufacturing, layout: validate_saved_dxf(
            path,
            manufacturing,
            layout=layout,
        ),
        load_reference=lambda path, digest, member: load_snapshot_reference(
            path,
            expected_source_sha256=digest,
            expected_member_mark=member,
        ),
        compare_reference=lambda groups, reference, part, disposition: (
            compare_groups_to_reference(
                groups,
                reference,
                part_number=part,
                internal_disposition=disposition,
            )
        ),
        evaluate_constraints=lambda contract, groups, available: (
            evaluate_human_constraints(
                contract,
                groups,
                output_available=available,
            )
        ),
        compile_bh_source=_compile_bh_candidate,
    )


@dataclass(frozen=True, slots=True)
class SampleRunResult:
    sample_id: str
    family: str
    source_sheet: str | None
    category: str | None
    evidence_level: EvidenceLevel
    status: FinalStatus
    reasons: tuple[str, ...]
    internal_disposition: str | None
    output_available: bool
    source_sha256_before: str
    source_sha256_after: str
    source_unchanged: bool
    candidate_path: str | None = None
    candidate_validation: dict[str, object] | None = None
    manufacturing_fingerprint: str | None = None
    profile: str | None = None
    nominal_length_mm: float | None = None
    assignment_signature: str | None = None
    hypothesis_count: int | None = None
    groups: tuple[dict[str, object], ...] = ()
    comparison_failed_keys: tuple[str, ...] = ()
    comparison_payload: dict[str, object] | None = None
    constraint_results: tuple[ConstraintResult, ...] = ()
    error_type: str | None = None
    error_message: str | None = None


def _group_payload(groups: tuple[Any, ...]) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "group_id": str(group.group_id),
            "roles": [role.value for role in group.roles],
            "quantity": int(group.quantity),
            "merge_authorized": bool(group.merge_authorized),
            "physical_plate_ids": [
                str(plate.plate_id) for plate in group.physical_plates
            ],
        }
        for group in groups
    )


def _number(value: float) -> float | None:
    return float(value) if isfinite(value) else None


def _comparison_payload(
    comparison: WholeDrawingComparison,
) -> dict[str, object]:
    return {
        "ok": comparison.ok,
        "failed_checks": list(comparison.failed_checks),
        "failed_check_keys": list(comparison.failed_check_keys),
        "evidence_warnings": list(comparison.evidence_warnings),
        "internal_disposition": comparison.internal_disposition,
        "plates": [
            {
                "output_group": item.output_group,
                "manual_label": item.manual_label,
                "family": item.family,
                "ok": item.ok,
                "failed_check_keys": list(item.failed_check_keys),
                "checks": dict(item.checks),
                "metrics": {key: _number(value) for key, value in item.metrics},
            }
            for item in comparison.comparisons
        ],
    }


def _candidate_purpose(disposition: str) -> OutputPurpose | None:
    if disposition == "auto_accept":
        return OutputPurpose.PRODUCTION
    if disposition == "review_required":
        return OutputPurpose.REVIEW
    return None


def _yellow_evidence(contract: SampleContract):
    matches = tuple(
        evidence
        for evidence in contract.evidence_files
        if Path(evidence.relative_path).name.startswith("03_正确结果_黄色_")
        and Path(evidence.relative_path).suffix.casefold() == ".dwg"
    )
    if len(matches) != 1:
        raise ValueError(
            f"{contract.sample_id} requires exactly one yellow reference, got {len(matches)}"
        )
    return matches[0]


def _failure_result(
    contract: SampleContract,
    *,
    source_before: str,
    source_after: str,
    error: Exception,
    hooks: RunnerHooks,
    internal_disposition: str | None = None,
) -> SampleRunResult:
    constraint_results: tuple[ConstraintResult, ...] = ()
    if contract.evidence_level is EvidenceLevel.HUMAN_CONSTRAINT:
        constraint_results = hooks.evaluate_constraints(
            contract,
            (),
            False,
        ).results
    status = classify_verdict(
        output_available=False,
        evidence_level=contract.evidence_level,
        complete_reference_passed=None,
        constraint_results=constraint_results,
        internal_disposition=internal_disposition,
    )
    return SampleRunResult(
        sample_id=contract.sample_id,
        family=contract.family,
        source_sheet=contract.source_sheet,
        category=contract.category,
        evidence_level=contract.evidence_level,
        status=status,
        reasons=(f"{type(error).__name__}: {error}",),
        internal_disposition=internal_disposition,
        output_available=False,
        source_sha256_before=source_before,
        source_sha256_after=source_after,
        source_unchanged=source_before == source_after == contract.original.sha256,
        constraint_results=constraint_results,
        error_type=type(error).__name__,
        error_message=str(error),
    )


def _evaluate_bh_sample(
    contract: SampleContract,
    *,
    source: Path,
    source_before: str,
    candidate_root: Path,
    hooks: RunnerHooks,
) -> SampleRunResult:
    compile_bh_source = hooks.compile_bh_source
    if compile_bh_source is None:
        return _failure_result(
            contract,
            source_before=source_before,
            source_after=_sha256(source),
            error=RuntimeError("BH acceptance compiler is not configured"),
            hooks=hooks,
        )
    try:
        compiled = compile_bh_source(source, candidate_root)
    except Exception as error:
        return _failure_result(
            contract,
            source_before=source_before,
            source_after=_sha256(source),
            error=error,
            hooks=hooks,
        )

    source_after = _sha256(source)
    disposition = str(compiled.internal_disposition)
    if source_after != source_before:
        return _failure_result(
            contract,
            source_before=source_before,
            source_after=source_after,
            error=RuntimeError("source changed before external evidence evaluation"),
            hooks=hooks,
            internal_disposition=disposition,
        )
    if contract.evidence_level is EvidenceLevel.COMPLETE_REFERENCE:
        return _failure_result(
            contract,
            source_before=source_before,
            source_after=source_after,
            error=RuntimeError("BH complete-reference comparison is not configured"),
            hooks=hooks,
            internal_disposition=disposition,
        )

    constraints: tuple[ConstraintResult, ...] = ()
    if contract.evidence_level is EvidenceLevel.HUMAN_CONSTRAINT:
        constraints = hooks.evaluate_constraints(contract, (), True).results
    status = classify_verdict(
        output_available=True,
        evidence_level=contract.evidence_level,
        complete_reference_passed=None,
        constraint_results=constraints,
        internal_disposition=disposition,
    )
    reasons = tuple(
        result.reason
        for result in constraints
        if result.status is not ConstraintStatus.PASS
    ) or ("现有外部证据未发现冲突，但不足以证明整图生产正确",)
    return SampleRunResult(
        sample_id=contract.sample_id,
        family=contract.family,
        source_sheet=contract.source_sheet,
        category=contract.category,
        evidence_level=contract.evidence_level,
        status=status,
        reasons=reasons,
        internal_disposition=disposition,
        output_available=True,
        source_sha256_before=source_before,
        source_sha256_after=source_after,
        source_unchanged=source_before == source_after == contract.original.sha256,
        candidate_path=str(Path(compiled.candidate_path).resolve()),
        candidate_validation=compiled.candidate_validation,
        manufacturing_fingerprint=compiled.manufacturing_fingerprint,
        profile=compiled.profile,
        nominal_length_mm=compiled.nominal_length_mm,
        hypothesis_count=compiled.hypothesis_count,
        constraint_results=constraints,
    )


def evaluate_sample(
    contract: SampleContract,
    *,
    sample_root: Path,
    snapshot_root: Path,
    candidate_root: Path,
    hooks: RunnerHooks | None = None,
) -> SampleRunResult:
    """Compile and freeze one source before loading its external answer."""

    active_hooks = hooks or _default_hooks()
    source = _within_root(Path(sample_root), contract.original.relative_path)
    source_before = _sha256(source)
    if source_before != contract.original.sha256:
        error = ValueError("source hash does not match the frozen evidence contract")
        return _failure_result(
            contract,
            source_before=source_before,
            source_after=source_before,
            error=error,
            hooks=active_hooks,
        )

    if contract.family == "BH":
        return _evaluate_bh_sample(
            contract,
            source=source,
            source_before=source_before,
            candidate_root=candidate_root,
            hooks=active_hooks,
        )
    if contract.family != "BOX":
        return _failure_result(
            contract,
            source_before=source_before,
            source_after=_sha256(source),
            error=ValueError(f"unsupported acceptance family: {contract.family}"),
            hooks=active_hooks,
        )

    try:
        core = active_hooks.compile_source(source)
    except Exception as error:
        return _failure_result(
            contract,
            source_before=source_before,
            source_after=_sha256(source),
            error=error,
            hooks=active_hooks,
        )

    disposition = str(core.proof_report.disposition.value)
    try:
        groups = active_hooks.group_outputs(core.manufacturing.physical_plates)
        candidate_root.mkdir(parents=True, exist_ok=True)
        candidate = candidate_root / f"{contract.sample_id}_external-candidate.dxf"
        purpose = _candidate_purpose(disposition)
        if purpose is None:
            raise RuntimeError(
                f"proof disposition {disposition!r} cannot freeze a candidate"
            )
        layout = active_hooks.write_candidate(core.manufacturing, candidate, purpose)
        candidate_validation = active_hooks.validate_candidate(
            candidate,
            core.manufacturing,
            layout,
        )
        if candidate_validation.get("ok") is not True:
            raise RuntimeError("candidate DXF failed writer/reopen validation")
    except Exception as error:
        return _failure_result(
            contract,
            source_before=source_before,
            source_after=_sha256(source),
            error=error,
            hooks=active_hooks,
            internal_disposition=disposition,
        )

    source_after = _sha256(source)
    if source_after != source_before:
        return _failure_result(
            contract,
            source_before=source_before,
            source_after=source_after,
            error=RuntimeError("source changed before external reference access"),
            hooks=active_hooks,
            internal_disposition=disposition,
        )

    comparison: WholeDrawingComparison | None = None
    constraints: tuple[ConstraintResult, ...] = ()
    if contract.evidence_level is EvidenceLevel.COMPLETE_REFERENCE:
        yellow = _yellow_evidence(contract)
        snapshot_path = Path(snapshot_root) / (
            f"{contract.sample_id}_correct-reference.json"
        )
        reference = active_hooks.load_reference(
            snapshot_path,
            yellow.sha256,
            contract.sample_id,
        )
        comparison = active_hooks.compare_reference(
            groups,
            reference,
            str(core.manufacturing.part_number),
            disposition,
        )
    elif contract.evidence_level is EvidenceLevel.HUMAN_CONSTRAINT:
        constraints = active_hooks.evaluate_constraints(
            contract,
            groups,
            True,
        ).results

    status = classify_verdict(
        output_available=True,
        evidence_level=contract.evidence_level,
        complete_reference_passed=None if comparison is None else comparison.ok,
        constraint_results=constraints,
        internal_disposition=disposition,
    )
    reasons = (
        tuple(comparison.failed_checks)
        if comparison is not None and not comparison.ok
        else tuple(
            result.reason
            for result in constraints
            if result.status is not ConstraintStatus.PASS
        )
    )
    if not reasons:
        reasons = (
            "完整外部答案逐板比较通过"
            if status is FinalStatus.PRODUCTION_PASS
            else "现有外部证据未发现冲突，但不足以证明整图生产正确"
        ,)

    assignment = getattr(getattr(core.search, "best", None), "assignment", None)
    return SampleRunResult(
        sample_id=contract.sample_id,
        family=contract.family,
        source_sheet=contract.source_sheet,
        category=contract.category,
        evidence_level=contract.evidence_level,
        status=status,
        reasons=reasons,
        internal_disposition=disposition,
        output_available=True,
        source_sha256_before=source_before,
        source_sha256_after=source_after,
        source_unchanged=source_before == source_after == contract.original.sha256,
        candidate_path=str(candidate.resolve()),
        candidate_validation=candidate_validation,
        manufacturing_fingerprint=str(core.manufacturing.fingerprint),
        profile=str(core.manufacturing.profile),
        nominal_length_mm=float(core.manufacturing.nominal_length_mm),
        assignment_signature=getattr(assignment, "signature", None),
        hypothesis_count=len(core.search.hypotheses),
        groups=_group_payload(groups),
        comparison_failed_keys=(
            () if comparison is None else comparison.failed_check_keys
        ),
        comparison_payload=(
            None if comparison is None else _comparison_payload(comparison)
        ),
        constraint_results=constraints,
    )


def _root_cause_clusters(results: tuple[Any, ...]) -> dict[str, list[str]]:
    clusters: defaultdict[str, set[str]] = defaultdict(set)
    for result in results:
        sample = result.sample_id
        if result.status is FinalStatus.EVIDENCE_INSUFFICIENT:
            clusters["evidence_gap"].add(sample)
        if result.status is FinalStatus.NO_OUTPUT:
            if result.error_type == "MetadataResolutionError":
                clusters["metadata_no_output"].add(sample)
            elif result.error_type == "AssemblyResolutionError":
                clusters["assembly_no_output"].add(sample)
            else:
                clusters["other_no_output"].add(sample)
        keys = set(result.comparison_failed_keys)
        if keys and result.internal_disposition == "auto_accept":
            clusters["auto_accept_geometry_mismatch"].add(sample)
        if "web_group_count" in keys:
            clusters["web_quantity_violation"].add(sample)
        if "flange_group_count" in keys:
            clusters["flange_quantity_violation"].add(sample)
        payload = result.comparison_payload or {}
        for plate in payload.get("plates", []):
            plate_keys = set(plate.get("failed_check_keys", []))
            family = plate.get("family")
            if family == "flange" and "role" in plate_keys:
                clusters["flange_role_violation"].add(sample)
            if family == "flange" and "contour" in plate_keys:
                clusters["flange_length_violation"].add(sample)
            if family == "web" and plate_keys & {
                "circular_hole_count",
                "circular_hole_centers",
                "circular_hole_radii",
                "inner_contour_count",
                "inner_contour_geometry",
            }:
                clusters["web_hole_violation"].add(sample)
        for constraint in result.constraint_results:
            if constraint.status is not ConstraintStatus.FAIL:
                continue
            mapping = {
                "web_quantity": "web_quantity_violation",
                "web_deduplication": "web_quantity_violation",
                "flange_deduplication": "flange_quantity_violation",
                "flange_role_order": "flange_role_violation",
                "flange_length": "flange_length_violation",
                "web_hole_spacing": "web_hole_violation",
            }
            if constraint.key in mapping:
                clusters[mapping[constraint.key]].add(sample)
    names = (
        "metadata_no_output",
        "assembly_no_output",
        "other_no_output",
        "auto_accept_geometry_mismatch",
        "web_quantity_violation",
        "flange_quantity_violation",
        "flange_role_violation",
        "flange_length_violation",
        "web_hole_violation",
        "evidence_gap",
    )
    return {name: sorted(clusters[name]) for name in names}


def summarize_results(results: tuple[Any, ...]) -> dict[str, object]:
    sample_ids = [result.sample_id for result in results]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("acceptance report contains duplicate sample IDs")
    diagnostic_passes = sorted(
        result.sample_id
        for result in results
        if result.evidence_level is EvidenceLevel.INTERNAL_DIAGNOSTIC
        and result.status is FinalStatus.PRODUCTION_PASS
    )
    if diagnostic_passes:
        raise ValueError(
            "diagnostic evidence cannot authorize production pass: "
            + ", ".join(diagnostic_passes)
        )
    counts = Counter(result.status.value for result in results)
    family_counts = Counter(result.family for result in results)
    auto_accept_external_failures = sorted(
        result.sample_id
        for result in results
        if result.internal_disposition == "auto_accept"
        and result.status in {FinalStatus.PRODUCTION_FAIL, FinalStatus.NO_OUTPUT}
    )
    return {
        "sample_count": len(results),
        "by_status": {status.value: counts[status.value] for status in FinalStatus},
        "by_family": {
            family: family_counts[family]
            for family in ("BH", "BOX")
        },
        "by_evidence_level": dict(
            sorted(Counter(result.evidence_level.value for result in results).items())
        ),
        "auto_accept_external_failures": auto_accept_external_failures,
        "diagnostic_production_passes": diagnostic_passes,
        "root_cause_clusters": _root_cause_clusters(results),
    }


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, ConstraintResult):
        return {
            "key": value.key,
            "status": value.status.value,
            "reason": value.reason,
        }
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _result_payload(result: SampleRunResult) -> dict[str, object]:
    return {
        field: _json_value(getattr(result, field))
        for field in result.__dataclass_fields__
    }


def _current_evidence_snapshot(
    contracts: tuple[SampleContract, ...],
    sample_root: Path,
) -> dict[str, str]:
    paths = {
        evidence.relative_path
        for contract in contracts
        for evidence in contract.evidence_files
    }
    return {
        relative: _sha256(_within_root(sample_root, relative))
        for relative in sorted(paths)
    }


def run_acceptance(
    *,
    sample_root: Path,
    classification_manifest: Path,
    snapshot_root: Path,
    artifact_root: Path,
    progress: Callable[[int, int, SampleRunResult], None] | None = None,
) -> dict[str, object]:
    root = Path(sample_root).resolve(strict=True)
    contracts = build_sample_contracts(
        root,
        classification_manifest=classification_manifest,
    )
    frozen_snapshot = source_snapshot(contracts)
    candidate_root = Path(artifact_root) / "candidates"
    results: list[SampleRunResult] = []
    for index, contract in enumerate(contracts, start=1):
        result = evaluate_sample(
            contract,
            sample_root=root,
            snapshot_root=snapshot_root,
            candidate_root=candidate_root,
        )
        results.append(result)
        if progress is not None:
            progress(index, len(contracts), result)
    materialized = tuple(results)
    after_snapshot = _current_evidence_snapshot(contracts, root)
    drift = sorted(
        relative
        for relative in set(frozen_snapshot) | set(after_snapshot)
        if frozen_snapshot.get(relative) != after_snapshot.get(relative)
    )
    return {
        "schema": "STEEL-DXF-PRODUCTION-STANDARD-ACCEPTANCE-1.0",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_root": str(root),
        "classification_manifest": str(
            Path(classification_manifest).resolve(strict=True)
        ),
        "snapshot_root": str(Path(snapshot_root).resolve(strict=True)),
        "artifact_root": str(Path(artifact_root).resolve()),
        "ground_truth_used_for_compilation": False,
        "internal_disposition_is_diagnostic_only": True,
        "source_evidence_unchanged": not drift,
        "source_evidence_drift": drift,
        "summary": summarize_results(materialized),
        "samples": [_result_payload(result) for result in materialized],
    }


def write_report(report: dict[str, object], path: Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return output


def write_markdown_report(report: dict[str, object], path: Path) -> Path:
    summary = report["summary"]
    assert isinstance(summary, dict)
    statuses = summary["by_status"]
    assert isinstance(statuses, dict)
    conflicts = summary["auto_accept_external_failures"]
    assert isinstance(conflicts, list)
    clusters = summary["root_cause_clusters"]
    assert isinstance(clusters, dict)
    lines = [
        "# BH/BOX 外部生产标准验收基线",
        "",
        "## 结论",
        "",
        f"- 样本总数：{summary['sample_count']}。",
        f"- 生产通过：{statuses['production_pass']}。",
        f"- 生产失败：{statuses['production_fail']}。",
        f"- 无输出：{statuses['no_output']}。",
        f"- 证据不足：{statuses['evidence_insufficient']}。",
        f"- 内部自动通过但外部失败：{len(conflicts)}。",
        f"- 所有冻结源证据未变化：{report['source_evidence_unchanged']}。",
        "",
        "## 内部自动通过与外部标准冲突",
        "",
        *(f"- `{sample}`" for sample in conflicts),
        "",
        "## 根因诊断簇",
        "",
    ]
    for name, samples in clusters.items():
        assert isinstance(samples, list)
        lines.append(f"- `{name}`：{len(samples)} 张。")
    lines.extend(
        (
            "",
            "## 判定原则",
            "",
            "- `auto_accept` 仅作诊断，不授权生产通过。",
            "- 18 张黄色答案执行逐板轮廓、数量、角色、圆孔和异形孔比较。",
            "- 68 张人工说明只判断其明确授权的约束；缺少唯一正确值时保持证据不足。",
            "- 10 张无外部答案样本不得判为生产通过。",
            "",
        )
    )
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return output
