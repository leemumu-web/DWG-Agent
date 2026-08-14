from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools.box_acceptance.constraints import ConstraintEvaluation
from tools.box_acceptance.contracts import (
    ConstraintResult,
    ConstraintStatus,
    EvidenceFile,
    EvidenceLevel,
    FinalStatus,
    SampleContract,
)
from tools.box_acceptance.geometry import WholeDrawingComparison
from tools.box_acceptance.runner import (
    RunnerHooks,
    evaluate_sample,
    summarize_results,
)


@dataclass(frozen=True)
class _Disposition:
    value: str


def _evidence(path: Path, root: Path) -> EvidenceFile:
    payload = path.read_bytes()
    return EvidenceFile(
        relative_path=path.relative_to(root).as_posix(),
        size=len(payload),
        sha256=sha256(payload).hexdigest(),
    )


def _complete_contract(root: Path) -> SampleContract:
    source = root / "01_全部原文件" / "sample.dxf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    yellow = (
        root
        / "03_第二张合并图_问题样本"
        / "category"
        / "sample"
        / "03_正确结果_黄色_sample.dwg"
    )
    yellow.parent.mkdir(parents=True)
    yellow.write_bytes(b"yellow")
    original = _evidence(source, root)
    answer = _evidence(yellow, root)
    return SampleContract(
        sample_id="sample",
        family="BOX",
        source_sheet="second",
        category="category",
        evidence_level=EvidenceLevel.COMPLETE_REFERENCE,
        original=original,
        evidence_files=(original, answer),
        constraints=(),
        human_wording=None,
    )


def _core() -> SimpleNamespace:
    manufacturing = SimpleNamespace(
        part_number="sample",
        proof_disposition="auto_accept",
        fingerprint="f" * 64,
        physical_plates=(),
        profile="BOX100*100*10*10",
        nominal_length_mm=1000.0,
    )
    return SimpleNamespace(
        manufacturing=manufacturing,
        proof_report=SimpleNamespace(disposition=_Disposition("auto_accept")),
        validation={"ok": True},
        search=SimpleNamespace(
            best=SimpleNamespace(assignment=SimpleNamespace(signature="assignment")),
            hypotheses=(object(),),
            diagnostics=(),
            search_complete=True,
        ),
        fingerprint=manufacturing.fingerprint,
    )


def _comparison(*, ok: bool) -> WholeDrawingComparison:
    return WholeDrawingComparison(
        ok=ok,
        comparisons=(),
        failed_checks=() if ok else ("flange group count mismatch",),
        failed_check_keys=() if ok else ("flange_group_count",),
        evidence_warnings=(),
        internal_disposition="auto_accept",
    )


def _hooks(events: list[str], *, comparison_ok: bool) -> RunnerHooks:
    def compile_source(path: Path):
        events.append("compile")
        return _core()

    def group_outputs(plates):
        events.append("group")
        return ()

    def write_candidate(manufacturing, path: Path, purpose):
        events.append("write")
        path.write_bytes(b"candidate")
        return object()

    def validate_candidate(path: Path, manufacturing, layout):
        events.append("validate")
        return {"ok": True, "checks": {"reopens": True}}

    def load_reference(path: Path, expected_sha256: str, member_mark: str):
        events.append("load_reference")
        return object()

    def compare_reference(groups, reference, part_number: str, internal: str):
        events.append("compare")
        return _comparison(ok=comparison_ok)

    def evaluate_constraints(contract, groups, output_available: bool):
        events.append("constraints")
        return ConstraintEvaluation(results=())

    return RunnerHooks(
        compile_source=compile_source,
        group_outputs=group_outputs,
        write_candidate=write_candidate,
        validate_candidate=validate_candidate,
        load_reference=load_reference,
        compare_reference=compare_reference,
        evaluate_constraints=evaluate_constraints,
    )


def test_complete_reference_is_loaded_only_after_source_and_candidate_freeze(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    contract = _complete_contract(root)
    events: list[str] = []

    result = evaluate_sample(
        contract,
        sample_root=root,
        snapshot_root=tmp_path / "snapshots",
        candidate_root=tmp_path / "candidates",
        hooks=_hooks(events, comparison_ok=True),
    )

    assert events == [
        "compile",
        "group",
        "write",
        "validate",
        "load_reference",
        "compare",
    ]
    assert result.status is FinalStatus.PRODUCTION_PASS


def test_internal_auto_accept_cannot_override_external_geometry_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    contract = _complete_contract(root)

    result = evaluate_sample(
        contract,
        sample_root=root,
        snapshot_root=tmp_path / "snapshots",
        candidate_root=tmp_path / "candidates",
        hooks=_hooks([], comparison_ok=False),
    )

    assert result.internal_disposition == "auto_accept"
    assert result.status is FinalStatus.PRODUCTION_FAIL
    assert "flange_group_count" in result.comparison_failed_keys


def test_compile_failure_still_evaluates_every_human_constraint_as_no_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    complete = _complete_contract(root)
    contract = SampleContract(
        sample_id=complete.sample_id,
        family="BOX",
        source_sheet="first",
        category="06_未拆板",
        evidence_level=EvidenceLevel.HUMAN_CONSTRAINT,
        original=complete.original,
        evidence_files=(complete.original,),
        constraints=(),
        human_wording="未拆板",
    )
    events: list[str] = []
    hooks = _hooks(events, comparison_ok=True)

    def fail_compile(path: Path):
        events.append("compile")
        raise RuntimeError("cannot compile")

    def constraints(contract, groups, output_available: bool):
        events.append("constraints")
        return ConstraintEvaluation(
            results=(
                ConstraintResult(
                    "formal_output_required",
                    ConstraintStatus.FAIL,
                    "没有正式输出",
                ),
            )
        )

    hooks = RunnerHooks(
        compile_source=fail_compile,
        group_outputs=hooks.group_outputs,
        write_candidate=hooks.write_candidate,
        validate_candidate=hooks.validate_candidate,
        load_reference=hooks.load_reference,
        compare_reference=hooks.compare_reference,
        evaluate_constraints=constraints,
    )

    result = evaluate_sample(
        contract,
        sample_root=root,
        snapshot_root=tmp_path / "snapshots",
        candidate_root=tmp_path / "candidates",
        hooks=hooks,
    )

    assert events == ["compile", "constraints"]
    assert result.status is FinalStatus.NO_OUTPUT
    assert result.constraint_results[0].status is ConstraintStatus.FAIL


def test_bh_contract_dispatches_only_to_bh_core_and_keeps_external_status_conservative(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    source = root / "01_全部原文件" / "bh-sample.dxf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"classified-bh-source")
    original = _evidence(source, root)
    contract = SampleContract(
        sample_id="bh-sample",
        source_sheet="first",
        category="06_未拆板",
        evidence_level=EvidenceLevel.HUMAN_CONSTRAINT,
        original=original,
        evidence_files=(original,),
        constraints=(),
        human_wording="未拆板",
        family="BH",
    )
    events: list[str] = []

    def reject_box_core(_path: Path):
        raise AssertionError("classified BH input reached the BOX core")

    def compile_bh_source(path: Path, candidate_root: Path):
        events.append("compile_bh")
        candidate_root.mkdir(parents=True, exist_ok=True)
        candidate = candidate_root / f"{path.stem}_external-candidate.dxf"
        candidate.write_bytes(b"bh-candidate")
        return SimpleNamespace(
            internal_disposition="auto_accept",
            candidate_path=candidate,
            candidate_validation={"ok": True},
            manufacturing_fingerprint="b" * 64,
            profile="BH700*400*16*30",
            nominal_length_mm=5700.0,
            hypothesis_count=1,
        )

    def constraints(contract, groups, output_available: bool):
        events.append("constraints")
        assert groups == ()
        assert output_available is True
        return ConstraintEvaluation(
            results=(
                ConstraintResult(
                    "formal_output_required",
                    ConstraintStatus.PASS,
                    "已形成正式候选产物",
                ),
            )
        )

    base = _hooks(events, comparison_ok=True)
    hooks = RunnerHooks(
        compile_source=reject_box_core,
        group_outputs=base.group_outputs,
        write_candidate=base.write_candidate,
        validate_candidate=base.validate_candidate,
        load_reference=base.load_reference,
        compare_reference=base.compare_reference,
        evaluate_constraints=constraints,
        compile_bh_source=compile_bh_source,
    )

    result = evaluate_sample(
        contract,
        sample_root=root,
        snapshot_root=tmp_path / "snapshots",
        candidate_root=tmp_path / "candidates",
        hooks=hooks,
    )

    assert events == ["compile_bh", "constraints"]
    assert result.internal_disposition == "auto_accept"
    assert result.output_available is True
    assert result.status is FinalStatus.EVIDENCE_INSUFFICIENT
    assert result.family == "BH"


def test_summary_keeps_four_states_exclusive_and_never_promotes_diagnostics() -> None:
    rows = [
        SimpleNamespace(
            sample_id=f"sample-{index}",
            family="BOX",
            status=status,
            evidence_level=level,
            internal_disposition="auto_accept" if index < 2 else None,
            comparison_failed_keys=("contour",) if index == 1 else (),
            constraint_results=(),
            comparison_payload=None,
            error_type=None,
        )
        for index, (status, level) in enumerate(
            (
                (FinalStatus.PRODUCTION_PASS, EvidenceLevel.COMPLETE_REFERENCE),
                (FinalStatus.PRODUCTION_FAIL, EvidenceLevel.COMPLETE_REFERENCE),
                (FinalStatus.NO_OUTPUT, EvidenceLevel.HUMAN_CONSTRAINT),
                (FinalStatus.EVIDENCE_INSUFFICIENT, EvidenceLevel.INTERNAL_DIAGNOSTIC),
            )
        )
    ]

    summary = summarize_results(tuple(rows))

    assert summary["sample_count"] == 4
    assert summary["by_status"] == {
        "production_pass": 1,
        "production_fail": 1,
        "no_output": 1,
        "evidence_insufficient": 1,
    }
    assert summary["by_family"] == {"BH": 0, "BOX": 4}
    assert summary["auto_accept_external_failures"] == ["sample-1"]
    assert summary["diagnostic_production_passes"] == []


def test_summary_rejects_internal_diagnostic_production_pass() -> None:
    row = SimpleNamespace(
        sample_id="diagnostic",
        family="BOX",
        status=FinalStatus.PRODUCTION_PASS,
        evidence_level=EvidenceLevel.INTERNAL_DIAGNOSTIC,
        internal_disposition="auto_accept",
        comparison_failed_keys=(),
        constraint_results=(),
        comparison_payload=None,
        error_type=None,
    )

    with pytest.raises(ValueError, match="diagnostic evidence"):
        summarize_results((row,))
