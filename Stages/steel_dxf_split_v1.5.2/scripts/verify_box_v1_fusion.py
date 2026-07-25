from __future__ import annotations

import argparse
from collections.abc import Mapping
from hashlib import sha256
import json
import os
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "src"):
    value = str(entry)
    if value not in sys.path:
        sys.path.insert(0, value)

from tools.compare_box_corpus import compare_corpus  # noqa: E402
from steel_dxf_split.box.provenance import (  # noqa: E402
    BOX_CORE_COMMIT,
    BOX_CORE_TAG,
    BOX_CORE_VERSION,
)
from steel_dxf_split.box.release import write_box_release_attestation  # noqa: E402


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(name: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"BOX release gate 的 {name} 不是 SHA-256。")
    return value


def _manifest_filename(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"BOX frozen manifest 的 {name} 路径无效。")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.name != value.split("/")[-1]:
        raise ValueError(f"BOX frozen manifest 的 {name} 路径不安全。")
    return path.name


def build_box_release_gate(
    report: Mapping[str, object],
    manifest_path: Path,
    inputs: Path,
    references: Path,
) -> dict[str, object]:
    """Bind a frozen 10/10 manifest to a complete BOX v1 acceptance report."""

    manifest_path = Path(manifest_path)
    inputs = Path(inputs)
    references = Path(references)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("BOX frozen manifest 顶层必须是 JSON object。")
    entries = manifest.get("entries")
    if (
        manifest.get("schema_version") != "BOX-SUPERVISED-MANIFEST-1.0"
        or manifest.get("status") != "frozen"
        or not isinstance(manifest.get("approved_by"), str)
        or not str(manifest["approved_by"]).strip()
        or not isinstance(manifest.get("approved_at"), str)
        or not str(manifest["approved_at"]).strip()
        or not isinstance(entries, list)
        or len(entries) != 20
    ):
        raise ValueError("BOX frozen manifest 未完成 20 对审批合同。")

    expected_core = {
        "version": BOX_CORE_VERSION,
        "tag": BOX_CORE_TAG,
        "commit": BOX_CORE_COMMIT,
    }
    samples = report.get("samples")
    read_only = report.get("read_only_corpus")
    if (
        report.get("schema") != "BOX-V1-FUSION-ACCEPTANCE-1.0"
        or report.get("core") != expected_core
        or report.get("compiler_imports_manual_reference") is not False
        or report.get("ground_truth_used_for_decision") is not False
        or report.get("missing_inputs") != []
        or report.get("missing_references") != []
        or report.get("sample_count") != 20
        or report.get("passed") != 20
        or report.get("failed") != 0
        or report.get("all_passed") is not True
        or not isinstance(samples, list)
        or len(samples) != 20
        or not isinstance(read_only, Mapping)
        or read_only.get("input_file_count") != 20
        or read_only.get("reference_file_count") != 20
        or read_only.get("inputs_unchanged") is not True
        or read_only.get("references_unchanged") is not True
    ):
        raise ValueError("BOX acceptance report 未通过完整 20 对只读门。")

    sample_by_member: dict[str, Mapping[str, object]] = {}
    for item in samples:
        if not isinstance(item, Mapping) or not isinstance(item.get("member"), str):
            raise ValueError("BOX acceptance report 含无效样本记录。")
        member = str(item["member"])
        if member in sample_by_member:
            raise ValueError("BOX acceptance report 含重复构件。")
        sample_by_member[member] = item

    input_names = {path.name for path in inputs.glob("*_拆板前.dxf") if path.is_file()}
    reference_names = {
        path.name for path in references.glob("*_拆板后.dxf") if path.is_file()
    }
    manifest_input_names: set[str] = set()
    manifest_reference_names: set[str] = set()
    pair_ids: set[str] = set()
    calibration_count = 0
    acceptance_count = 0
    verdicts: list[dict[str, object]] = []

    for raw_entry in entries:
        if not isinstance(raw_entry, Mapping):
            raise ValueError("BOX frozen manifest 含无效条目。")
        pair_id = raw_entry.get("pair_id")
        partition = raw_entry.get("partition")
        if not isinstance(pair_id, str) or pair_id in pair_ids:
            raise ValueError("BOX frozen manifest 的 pair_id 无效或重复。")
        pair_ids.add(pair_id)
        if partition == "calibration":
            calibration_count += 1
        elif partition == "acceptance":
            acceptance_count += 1
        else:
            raise ValueError("BOX frozen manifest 的分区无效。")

        before_name = _manifest_filename("before", raw_entry.get("before_path"))
        after_name = _manifest_filename("after", raw_entry.get("after_path"))
        manifest_input_names.add(before_name)
        manifest_reference_names.add(after_name)
        before_path = inputs / before_name
        after_path = references / after_name
        expected_before_hash = _require_sha256(
            "before_sha256", raw_entry.get("before_sha256")
        )
        expected_after_hash = _require_sha256(
            "after_sha256", raw_entry.get("after_sha256")
        )
        if (
            not before_path.is_file()
            or not after_path.is_file()
            or _file_sha256(before_path) != expected_before_hash
            or _file_sha256(after_path) != expected_after_hash
        ):
            raise ValueError(f"BOX frozen manifest 文件哈希漂移：{pair_id}")

        suffix = "_拆板前.dxf"
        if not before_name.endswith(suffix):
            raise ValueError("BOX frozen manifest 的源文件命名无效。")
        member = before_name[: -len(suffix)]
        sample = sample_by_member.get(member)
        if sample is None:
            raise ValueError(f"BOX acceptance report 缺少构件：{member}")
        proof = sample.get("proof_report")
        search = sample.get("search_status")
        saved = sample.get("saved_dxf")
        checks = sample.get("checks")
        comparisons = sample.get("comparisons")
        if sample.get("ground_truth_used_for_decision") is not False:
            raise ValueError(f"BOX 人工 ground-truth 进入生产决策：{member}")
        if (
            sample.get("source_file_sha256") != expected_before_hash
            or sample.get("proof_disposition") != "auto_accept"
            or not isinstance(proof, Mapping)
            or proof.get("disposition") != "auto_accept"
            or proof.get("search_complete") is not True
            or proof.get("blocking_obligation_ids") != []
            or not isinstance(search, Mapping)
            or search.get("search_complete") is not True
            or not isinstance(saved, Mapping)
            or saved.get("ok") is not True
            or not isinstance(checks, Mapping)
            or not checks
            or not all(value is True for value in checks.values())
            or not isinstance(comparisons, (list, tuple))
            or not comparisons
            or not all(
                isinstance(comparison, Mapping)
                and comparison.get("ok") is True
                for comparison in comparisons
            )
            or sample.get("ok") is not True
        ):
            raise ValueError(f"BOX acceptance verdict 不完整：{member}")
        verdicts.append(
            {
                "pair_id": pair_id,
                "member": member,
                "partition": partition,
                "before_sha256": expected_before_hash,
                "after_sha256": expected_after_hash,
                "source_geometry_fingerprint": _require_sha256(
                    "source_geometry_fingerprint",
                    sample.get("source_geometry_fingerprint"),
                ),
                "manufacturing_fingerprint": _require_sha256(
                    "manufacturing_fingerprint",
                    sample.get("manufacturing_fingerprint"),
                ),
                "proof_disposition": "auto_accept",
                "passed": True,
            }
        )

    if (
        calibration_count != 10
        or acceptance_count != 10
        or input_names != manifest_input_names
        or reference_names != manifest_reference_names
        or set(sample_by_member)
        != {verdict["member"] for verdict in verdicts}
    ):
        raise ValueError("BOX frozen manifest、目录和 10/10 分区不一致。")

    manifest_fingerprint = sha256(_canonical_json(manifest)).hexdigest()
    gate: dict[str, object] = {
        "schema": "BOX-V1-RELEASE-GATE-1.0",
        "passed": True,
        "core": expected_core,
        "pair_count": 20,
        "calibration_count": calibration_count,
        "acceptance_count": acceptance_count,
        "manifest_file_sha256": _file_sha256(manifest_path),
        "manifest_fingerprint": manifest_fingerprint,
        "acceptance_schema": report["schema"],
        "thresholds": manifest.get("thresholds"),
        "checks": {
            "frozen_manifest_approved": True,
            "manifest_files_match": True,
            "acceptance_report_complete": True,
            "all_pair_verdicts_pass": True,
            "ground_truth_firewall": True,
            "read_only_corpus_unchanged": True,
        },
        "verdicts": sorted(verdicts, key=lambda item: str(item["pair_id"])),
    }
    return {
        **gate,
        "gate_fingerprint": sha256(_canonical_json(gate)).hexdigest(),
    }


def _write_report(path: Path, report: dict[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.pending")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                report,
                stream,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_box_release_artifacts(
    report: Mapping[str, object],
    *,
    manifest_path: Path,
    inputs: Path,
    references: Path,
    gate_path: Path,
    attestation_path: Path,
) -> dict[str, object]:
    """Write the audited gate and its implementation-bound attestation."""

    gate = build_box_release_gate(report, manifest_path, inputs, references)
    _write_report(gate_path, gate)
    write_box_release_attestation(
        attestation_path,
        pair_count=int(gate["pair_count"]),
        calibration_count=int(gate["calibration_count"]),
        acceptance_count=int(gate["acceptance_count"]),
        manifest_fingerprint=str(gate["manifest_fingerprint"]),
        gate_fingerprint=str(gate["gate_fingerprint"]),
    )
    return gate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="离线验证 BOX v1 源码融合与权威拆板前后 DXF 语料。"
    )
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--references", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--candidate-root", type=Path, default=None)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="冻结且已审批的 10/10 BOX 监督 manifest。",
    )
    parser.add_argument(
        "--release-gate-output",
        type=Path,
        default=None,
        help="完整 20 对通过后写入的可审计 release gate JSON。",
    )
    parser.add_argument(
        "--emit-release-attestation",
        type=Path,
        default=None,
        help="与 gate 和当前实现绑定的 BOX release attestation。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    release_options = (
        args.manifest,
        args.release_gate_output,
        args.emit_release_attestation,
    )
    if any(value is not None for value in release_options) and not all(
        value is not None for value in release_options
    ):
        parser.error(
            "--manifest、--release-gate-output 与 "
            "--emit-release-attestation 必须同时提供"
        )
    report = compare_corpus(
        args.inputs,
        args.references,
        candidate_root=args.candidate_root,
    )
    if args.output is not None:
        _write_report(args.output, report)
    gate: dict[str, object] | None = None
    if all(value is not None for value in release_options):
        assert args.manifest is not None
        assert args.release_gate_output is not None
        assert args.emit_release_attestation is not None
        gate = write_box_release_artifacts(
            report,
            manifest_path=args.manifest,
            inputs=args.inputs,
            references=args.references,
            gate_path=args.release_gate_output,
            attestation_path=args.emit_release_attestation,
        )
    print(
        json.dumps(
            {
                "schema": report["schema"],
                "sample_count": report["sample_count"],
                "passed": report["passed"],
                "failed": report["failed"],
                "all_passed": report["all_passed"],
                "inputs_unchanged": report["read_only_corpus"][
                    "inputs_unchanged"
                ],
                "references_unchanged": report["read_only_corpus"][
                    "references_unchanged"
                ],
                "release_gate_passed": (
                    None if gate is None else gate["passed"]
                ),
                "gate_fingerprint": (
                    None if gate is None else gate["gate_fingerprint"]
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
