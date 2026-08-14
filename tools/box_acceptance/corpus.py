from __future__ import annotations

import json
from collections import Counter
from hashlib import sha256
from pathlib import Path

from .contracts import (
    AcceptanceConstraint,
    EvidenceFile,
    EvidenceLevel,
    SampleContract,
)


_CLASSIFIED_INPUT_SCHEMA = "STEEL-DXF-CLASSIFIED-SPLIT-INPUT-1.0"


_CATEGORY_CONSTRAINTS: dict[str, tuple[AcceptanceConstraint, ...]] = {
    "01_上下翼缘板拆反且上翼缘长度错误": (
        AcceptanceConstraint("flange_role_order", "上下翼缘角色不得互换"),
        AcceptanceConstraint("flange_length", "翼缘实际长度必须符合生产标准"),
    ),
    "02_腹板多拆且翼缘板长度错误": (
        AcceptanceConstraint("web_quantity", "不得多拆腹板"),
        AcceptanceConstraint("flange_length", "翼缘实际长度必须符合生产标准"),
    ),
    "03_腹板孔距错误": (
        AcceptanceConstraint("web_hole_spacing", "腹板孔距必须符合生产标准"),
    ),
    "04_相同腹板被重复拆分": (
        AcceptanceConstraint("web_deduplication", "相同腹板必须合并数量"),
    ),
    "05_相同腹板和翼缘板被重复拆分": (
        AcceptanceConstraint("web_deduplication", "相同腹板必须合并数量"),
        AcceptanceConstraint("flange_deduplication", "相同翼缘必须合并数量"),
    ),
    "06_未拆板": (
        AcceptanceConstraint("formal_output_required", "必须形成正式拆板结果"),
    ),
}


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_file(path: Path, root: Path) -> EvidenceFile:
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"证据路径越界: {path}") from error
    if not resolved.is_file():
        raise ValueError(f"证据不是文件: {path}")
    return EvidenceFile(
        relative_path=relative.as_posix(),
        size=resolved.stat().st_size,
        sha256=_file_sha256(resolved),
    )


def _single_file(directory: Path, pattern: str, label: str) -> Path:
    matches = tuple(path for path in directory.glob(pattern) if path.is_file())
    if len(matches) != 1:
        raise ValueError(
            f"{directory.name} 的{label}数量必须为 1，实际为 {len(matches)}"
        )
    return matches[0]


def _problem_records(root: Path) -> dict[str, tuple[str, Path]]:
    records: dict[str, tuple[str, Path]] = {}
    sheets = (
        ("first", root / "02_第一张合并图_问题样本"),
        ("second", root / "03_第二张合并图_问题样本"),
    )
    for sheet, sheet_root in sheets:
        if not sheet_root.is_dir():
            raise ValueError(f"缺少问题样本目录: {sheet_root}")
        for analysis in sorted(sheet_root.rglob("错误分析.txt")):
            sample_dir = analysis.parent
            sample_id = sample_dir.name
            if sample_id in records:
                raise ValueError(f"重复问题样本: {sample_id}")
            records[sample_id] = (sheet, sample_dir)
    return records


def _human_wording(analysis_path: Path) -> str | None:
    text = analysis_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("人工原始表述："):
            return line.removeprefix("人工原始表述：").strip() or None
    return None


def _constraints_from_human_wording(
    wording: str | None,
) -> tuple[AcceptanceConstraint, ...]:
    if not wording:
        return ()
    constraints: list[AcceptanceConstraint] = []
    if "翼缘" in wording and "长度错误" in wording:
        constraints.append(
            AcceptanceConstraint("flange_length", "翼缘实际长度必须符合生产标准")
        )
    return tuple(constraints)


def _merge_constraints(
    *groups: tuple[AcceptanceConstraint, ...],
) -> tuple[AcceptanceConstraint, ...]:
    by_key: dict[str, AcceptanceConstraint] = {}
    for group in groups:
        for constraint in group:
            by_key.setdefault(constraint.key, constraint)
    return tuple(by_key.values())


def _classified_families(
    path: Path,
    originals: tuple[Path, ...],
) -> dict[str, str]:
    manifest = Path(path)
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError(f"分类清单不可读取: {manifest}")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"分类清单不是有效 JSON: {manifest}") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "items"}:
        raise ValueError("分类清单顶层字段无效")
    if payload.get("schema") != _CLASSIFIED_INPUT_SCHEMA:
        raise ValueError("分类清单 schema 无效")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("分类清单 items 必须是数组")

    family_by_name: dict[str, str] = {}
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict) or set(item) != {"file_name", "family"}:
            raise ValueError(f"分类清单第 {index} 项字段无效")
        file_name = item.get("file_name")
        family = item.get("family")
        if (
            not isinstance(file_name, str)
            or Path(file_name).name != file_name
            or Path(file_name).suffix.casefold() != ".dxf"
        ):
            raise ValueError(f"分类清单第 {index} 项 file_name 无效")
        if family not in {"BH", "BOX"}:
            raise ValueError(f"分类清单类型不支持: {family}")
        key = file_name.casefold()
        if key in family_by_name:
            raise ValueError(f"分类清单文件名重复: {file_name}")
        family_by_name[key] = family

    original_names = {original.name.casefold() for original in originals}
    extra = sorted(set(family_by_name) - original_names)
    missing = sorted(original_names - set(family_by_name))
    if extra:
        raise ValueError(f"分类清单包含额外文件: {extra[0]}")
    if missing:
        raise ValueError(f"分类清单缺少原文件: {missing[0]}")
    return family_by_name


def build_sample_contracts(
    root: Path,
    *,
    classification_manifest: Path | None = None,
) -> tuple[SampleContract, ...]:
    corpus_root = Path(root).resolve(strict=True)
    originals_root = corpus_root / "01_全部原文件"
    if not originals_root.is_dir():
        raise ValueError(f"缺少全部原文件目录: {originals_root}")
    original_paths = tuple(sorted(originals_root.glob("*.dxf")))
    original_by_id = {path.stem: path for path in original_paths}
    if len(original_by_id) != len(original_paths):
        raise ValueError("全部原文件包含重复编号")
    records = _problem_records(corpus_root)
    missing = sorted(set(records) - set(original_by_id))
    if missing:
        raise ValueError("问题样本缺少全部原文件: " + ", ".join(missing))
    if len(original_by_id) != 96:
        raise ValueError(f"全部原文件必须为 96 张，实际为 {len(original_by_id)}")
    first_count = sum(sheet == "first" for sheet, _ in records.values())
    second_count = sum(sheet == "second" for sheet, _ in records.values())
    if (first_count, second_count) != (68, 18):
        raise ValueError(
            "问题样本数量必须为第一张 68、第二张 18，实际为 "
            f"{first_count}、{second_count}"
        )
    if classification_manifest is None:
        raise ValueError("外部验收必须提供冻结的 BH/BOX 分类清单")
    family_by_name = _classified_families(classification_manifest, original_paths)

    contracts: list[SampleContract] = []
    for sample_id, original_path in sorted(original_by_id.items()):
        original = _evidence_file(original_path, corpus_root)
        record = records.get(sample_id)
        if record is None:
            contracts.append(
                SampleContract(
                    sample_id=sample_id,
                    family=family_by_name[original_path.name.casefold()],
                    source_sheet=None,
                    category=None,
                    evidence_level=EvidenceLevel.INTERNAL_DIAGNOSTIC,
                    original=original,
                    evidence_files=(original,),
                    constraints=(),
                    human_wording=None,
                )
            )
            continue

        sheet, sample_dir = record
        category = sample_dir.parent.name
        analysis_path = _single_file(sample_dir, "错误分析.txt", "错误分析")
        human_wording = _human_wording(analysis_path)
        extracted_original = _single_file(sample_dir, "01_原图_*.dxf", "提取原图")
        wrong_result = _single_file(sample_dir, "02_程序错误结果*", "程序错误结果")
        evidence_paths = [extracted_original, wrong_result, analysis_path]
        if sheet == "second":
            correct_result = _single_file(
                sample_dir,
                "03_正确结果_黄色_*.dwg",
                "黄色正确结果",
            )
            evidence_paths.append(correct_result)
            evidence_level = EvidenceLevel.COMPLETE_REFERENCE
            constraints: tuple[AcceptanceConstraint, ...] = ()
        else:
            if category not in _CATEGORY_CONSTRAINTS:
                raise ValueError(f"未知第一张合并图分类: {category}")
            evidence_level = EvidenceLevel.HUMAN_CONSTRAINT
            constraints = _merge_constraints(
                _CATEGORY_CONSTRAINTS[category],
                _constraints_from_human_wording(human_wording),
            )
        evidence_files = (
            original,
            *(_evidence_file(path, corpus_root) for path in evidence_paths),
        )
        contracts.append(
            SampleContract(
                sample_id=sample_id,
                family=family_by_name[original_path.name.casefold()],
                source_sheet=sheet,
                category=category,
                evidence_level=evidence_level,
                original=original,
                evidence_files=evidence_files,
                constraints=constraints,
                human_wording=human_wording,
            )
        )
    return tuple(contracts)


def source_snapshot(contracts: tuple[SampleContract, ...]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for contract in contracts:
        for evidence in contract.evidence_files:
            existing = snapshot.get(evidence.relative_path)
            if existing is not None and existing != evidence.sha256:
                raise ValueError(f"同一路径出现不同摘要: {evidence.relative_path}")
            snapshot[evidence.relative_path] = evidence.sha256
    return dict(sorted(snapshot.items()))


def _evidence_payload(evidence: EvidenceFile) -> dict[str, object]:
    return {
        "relative_path": evidence.relative_path,
        "size": evidence.size,
        "sha256": evidence.sha256,
    }


def build_contract_manifest(
    root: Path,
    *,
    classification_manifest: Path | None = None,
) -> dict[str, object]:
    corpus_root = Path(root).resolve(strict=True)
    contracts = build_sample_contracts(
        corpus_root,
        classification_manifest=classification_manifest,
    )
    counts = Counter(contract.evidence_level.value for contract in contracts)
    samples: list[dict[str, object]] = []
    for contract in contracts:
        historical_wrong_results = tuple(
            evidence
            for evidence in contract.evidence_files
            if Path(evidence.relative_path).name.startswith("02_程序错误结果")
            and Path(evidence.relative_path).suffix.casefold() == ".dwg"
        )
        if contract.source_sheet is None:
            if historical_wrong_results:
                raise ValueError(
                    f"{contract.sample_id} 非问题样本却包含历史错误结果"
                )
            historical_wrong_result: dict[str, object] | None = None
        else:
            if len(historical_wrong_results) != 1:
                raise ValueError(
                    f"{contract.sample_id} 必须有且仅有一个历史错误结果"
                )
            historical_wrong_result = _evidence_payload(
                historical_wrong_results[0]
            )
        complete_references = tuple(
            evidence
            for evidence in contract.evidence_files
            if Path(evidence.relative_path).name.startswith("03_正确结果_黄色_")
            and Path(evidence.relative_path).suffix.casefold() == ".dwg"
        )
        if contract.evidence_level is EvidenceLevel.COMPLETE_REFERENCE:
            if len(complete_references) != 1:
                raise ValueError(
                    f"{contract.sample_id} 必须有且仅有一个黄色正确结果"
                )
            complete_reference: dict[str, object] | None = _evidence_payload(
                complete_references[0]
            )
        else:
            if complete_references:
                raise ValueError(
                    f"{contract.sample_id} 非完整答案样本却包含黄色正确结果"
                )
            complete_reference = None
        samples.append(
            {
                "sample_id": contract.sample_id,
                "family": contract.family,
                "source_sheet": contract.source_sheet,
                "category": contract.category,
                "evidence_level": contract.evidence_level.value,
                "original": _evidence_payload(contract.original),
                "complete_reference": complete_reference,
                "historical_wrong_result": historical_wrong_result,
                "constraints": [
                    {"key": item.key, "description": item.description}
                    for item in contract.constraints
                ],
                "human_wording": contract.human_wording,
            }
        )
    return {
        "schema": "BOX-EXTERNAL-ACCEPTANCE-CONTRACTS-1.0",
        "corpus_root": str(corpus_root),
        "sample_count": len(contracts),
        "counts": {
            level.value: counts[level.value]
            for level in EvidenceLevel
        },
        "source_snapshot": source_snapshot(contracts),
        "samples": samples,
    }


def write_contract_manifest(
    root: Path,
    output_path: Path,
    *,
    classification_manifest: Path | None = None,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            build_contract_manifest(
                root,
                classification_manifest=classification_manifest,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return output
