"""Strict acceptance runner for deduplication-only historical BOX samples."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from steel_dxf_split.box.compiler import compile_box_core
from steel_dxf_split.box.contracts import BoxSourceContract
from steel_dxf_split.box.equivalence import group_equivalent_plate_pairs
from steel_dxf_split.box.validator import validate_saved_dxf
from steel_dxf_split.box.writer import OutputPurpose, write_box_clean

from .historical_delta import compare_historical_delta
from .historical_result import load_historical_result


_DEDUPLICATION_KEYS = frozenset({"web_deduplication", "flange_deduplication"})


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _constraint_keys(sample: dict[str, object]) -> frozenset[str]:
    raw = sample.get("constraints")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(
        str(item["key"])
        for item in raw
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    )


def select_deduplication_only_samples(
    manifest: dict[str, object],
) -> tuple[dict[str, object], ...]:
    raw_samples = manifest.get("samples")
    if not isinstance(raw_samples, list):
        raise ValueError("historical manifest samples must be an array")
    selected: list[dict[str, object]] = []
    for raw in raw_samples:
        if not isinstance(raw, dict):
            raise ValueError("historical manifest sample must be an object")
        keys = _constraint_keys(raw)
        if (
            raw.get("family") == "BOX"
            and raw.get("source_sheet") == "first"
            and isinstance(raw.get("historical_wrong_result"), dict)
            and "web_deduplication" in keys
            and keys <= _DEDUPLICATION_KEYS
        ):
            selected.append(raw)
    return tuple(selected)


def allowed_merge_families(sample: dict[str, object]) -> frozenset[str]:
    keys = _constraint_keys(sample)
    result: set[str] = set()
    if "web_deduplication" in keys:
        result.add("web")
    if "flange_deduplication" in keys:
        result.add("flange")
    return frozenset(result)


def summarize_historical_results(
    results: tuple[dict[str, object], ...],
) -> dict[str, object]:
    failed = tuple(item for item in results if item.get("ok") is False)
    errors = tuple(item for item in results if item.get("error_type") is not None)
    counts: Counter[str] = Counter(
        str(change)
        for item in failed
        for change in item.get("forbidden_changes", [])
        if isinstance(change, str)
    )
    return {
        "sample_count": len(results),
        "passed": sum(item.get("ok") is True for item in results),
        "failed": len(failed),
        "errors": len(errors),
        "auto_accept_external_failures": [
            str(item["sample_id"])
            for item in failed
            if item.get("internal_disposition") == "auto_accept"
        ],
        "forbidden_change_counts": dict(sorted(counts.items())),
    }


def _comparison_payload(verdict: Any) -> dict[str, object]:
    return {
        "failed_checks": list(verdict.comparison.failed_checks),
        "failed_check_keys": list(verdict.comparison.failed_check_keys),
        "plates": [
            {
                "output_group": item.output_group,
                "manual_label": item.manual_label,
                "family": item.family,
                "checks": dict(item.checks),
                "metrics": dict(item.metrics),
            }
            for item in verdict.comparison.comparisons
        ],
    }


def run_historical_acceptance(
    *,
    manifest_path: Path,
    snapshot_root: Path,
    artifact_root: Path,
    expected_count: int = 49,
) -> dict[str, object]:
    manifest_file = Path(manifest_path).resolve(strict=True)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("historical manifest must be an object")
    corpus_root = Path(str(manifest["corpus_root"])).resolve(strict=True)
    snapshots = Path(snapshot_root).resolve(strict=True)
    artifacts = Path(artifact_root).resolve()
    selected = select_deduplication_only_samples(manifest)
    if len(selected) != expected_count:
        raise ValueError(
            f"deduplication-only sample count mismatch: {len(selected)} != {expected_count}"
        )

    results: list[dict[str, object]] = []
    for sample in selected:
        sample_id = str(sample["sample_id"])
        original = sample["original"]
        historical = sample["historical_wrong_result"]
        assert isinstance(original, dict) and isinstance(historical, dict)
        source = (corpus_root / str(original["relative_path"])).resolve(strict=True)
        snapshot_path = snapshots / f"{sample_id}_historical-wrong-result.json"
        source_before = _sha256(source)
        row: dict[str, object] = {
            "sample_id": sample_id,
            "category": sample.get("category"),
            "source_path": str(source),
            "source_sha256_expected": original["sha256"],
            "source_sha256_before": source_before,
            "historical_relative_path": historical["relative_path"],
            "historical_sha256": historical["sha256"],
            "snapshot_path": str(snapshot_path.resolve()),
            "allowed_merge_families": sorted(allowed_merge_families(sample)),
        }
        try:
            if source_before != str(original["sha256"]):
                raise ValueError("original source hash drifted before compilation")
            core = compile_box_core(source, BoxSourceContract())
            groups = group_equivalent_plate_pairs(core.manufacturing.physical_plates)
            candidate = artifacts / "candidates" / f"{sample_id}_candidate.dxf"
            layout = write_box_clean(
                core.manufacturing,
                candidate,
                purpose=OutputPurpose.PRODUCTION,
            )
            candidate_validation = validate_saved_dxf(
                candidate,
                core.manufacturing,
                layout=layout,
            )
            if candidate_validation.get("ok") is not True:
                raise RuntimeError("saved candidate failed reopen validation")
            old = load_historical_result(
                snapshot_path,
                expected_source_sha256=str(historical["sha256"]),
                expected_member_mark=sample_id,
            )
            verdict = compare_historical_delta(
                groups,
                old,
                part_number=core.manufacturing.part_number,
                allowed_merge_families=allowed_merge_families(sample),
            )
            source_after = _sha256(source)
            if source_after != source_before:
                raise RuntimeError("original source hash drifted after acceptance")
            row.update(
                {
                    "ok": verdict.ok,
                    "internal_disposition": core.proof_report.disposition.value,
                    "candidate_path": str(candidate.resolve()),
                    "candidate_sha256": _sha256(candidate),
                    "candidate_validation": candidate_validation,
                    "source_sha256_after": source_after,
                    "source_unchanged": True,
                    "groups": [
                        {
                            "group_id": group.group_id,
                            "roles": [role.value for role in group.roles],
                            "quantity": group.quantity,
                            "merge_authorized": group.merge_authorized,
                        }
                        for group in groups
                    ],
                    "allowed_merges": list(verdict.allowed_merges),
                    "forbidden_changes": list(verdict.forbidden_changes),
                    "comparison": _comparison_payload(verdict),
                    "error_type": None,
                    "error_message": None,
                }
            )
        except Exception as error:
            row.update(
                {
                    "ok": False,
                    "source_sha256_after": _sha256(source),
                    "source_unchanged": _sha256(source) == source_before,
                    "allowed_merges": [],
                    "forbidden_changes": ["acceptance_error"],
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                }
            )
        results.append(row)

    frozen_results = tuple(results)
    return {
        "schema": "BOX-HISTORICAL-DEDUPLICATION-ACCEPTANCE-1.0",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "manifest_path": str(manifest_file),
        "snapshot_root": str(snapshots),
        "artifact_root": str(artifacts),
        "external_historical_evidence_is_authority": True,
        "internal_disposition_is_diagnostic_only": True,
        "summary": summarize_historical_results(frozen_results),
        "samples": list(frozen_results),
    }


def write_historical_report(report: dict[str, object], path: Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return output


def write_historical_markdown(report: dict[str, object], path: Path) -> Path:
    summary = report["summary"]
    assert isinstance(summary, dict)
    lines = [
        "# BOX 49 张历史错误结果严格验收",
        "",
        "## 结论",
        "",
        f"- 样本数：{summary['sample_count']}。",
        f"- 严格通过：{summary['passed']}。",
        f"- 严格失败：{summary['failed']}。",
        f"- 执行错误：{summary['errors']}。",
        "- 判定依据：旧程序错误结果中的制造板件；程序 `auto_accept` 仅作诊断。",
        "- 允许变化：人工明确要求的等价板数量归并；轮廓、孔、角色及其他分组均不得变化。",
        "",
        "## 逐张结果",
        "",
    ]
    samples = report["samples"]
    assert isinstance(samples, list)
    for sample in samples:
        assert isinstance(sample, dict)
        lines.append(
            f"- `{sample['sample_id']}`："
            f"{'通过' if sample.get('ok') is True else '失败'}；"
            f"允许归并={','.join(sample.get('allowed_merges', [])) or '-'}；"
            f"禁止变化={','.join(sample.get('forbidden_changes', [])) or '-'}。"
        )
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return output
