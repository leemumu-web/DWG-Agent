from __future__ import annotations

from pathlib import Path
import json

import pytest
from tools.box_acceptance.contracts import (
    ConstraintResult,
    ConstraintStatus,
    EvidenceLevel,
    FinalStatus,
    classify_verdict,
)
from tools.box_acceptance.corpus import (
    build_contract_manifest,
    build_sample_contracts,
    source_snapshot,
    write_contract_manifest,
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _sample_tree(root: Path) -> Path:
    originals = root / "01_全部原文件"
    for number in range(96):
        _write(originals / f"sample-{number:02d}.dxf", f"original-{number}")

    first = root / "02_第一张合并图_问题样本"
    first_categories = (
        "01_上下翼缘板拆反且上翼缘长度错误",
        "02_腹板多拆且翼缘板长度错误",
        "03_腹板孔距错误",
        "04_相同腹板被重复拆分",
        "05_相同腹板和翼缘板被重复拆分",
        "06_未拆板",
    )
    for number in range(68):
        sample_id = f"sample-{number:02d}"
        category = first_categories[number % len(first_categories)]
        sample = first / category / sample_id
        _write(sample / f"01_原图_{sample_id}.dxf", f"copy-{number}")
        _write(sample / f"02_程序错误结果_{sample_id}.dwg", f"wrong-{number}")
        _write(
            sample / "错误分析.txt",
            "\n".join(
                (
                    f"编号：{sample_id}",
                    f"归一化错误分类：{category[3:]}",
                    "人工原始表述：人工确认的生产错误",
                    "判定说明：不得以程序自动通过代替生产正确。",
                )
            ),
        )

    second = root / "03_第二张合并图_问题样本"
    category = "01_相同腹板未去重_不同翼缘被误合并"
    for number in range(68, 86):
        sample_id = f"sample-{number:02d}"
        sample = second / category / sample_id
        _write(sample / f"01_原图_{sample_id}.dxf", f"copy-{number}")
        _write(sample / f"02_程序错误结果_白色_{sample_id}.dwg", f"wrong-{number}")
        _write(sample / f"03_正确结果_黄色_{sample_id}.dwg", f"correct-{number}")
        _write(
            sample / "错误分析.txt",
            "\n".join(
                (
                    f"编号：{sample_id}",
                    "问题分类：相同腹板未去重，不同翼缘被误合并",
                    "判定说明：黄色结果是完整正确答案。",
                )
            ),
        )
    return root


def _classification_manifest(root: Path, *, bh_ids: set[str] | None = None) -> Path:
    bh_ids = bh_ids or set()
    items = [
        {
            "file_name": path.name,
            "family": "BH" if path.stem in bh_ids else "BOX",
        }
        for path in sorted((root / "01_全部原文件").glob("*.dxf"))
    ]
    path = root / "classification-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": "STEEL-DXF-CLASSIFIED-SPLIT-INPUT-1.0",
                "items": items,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("output_available", "evidence_level", "reference_passed", "constraints", "expected"),
    (
        (
            True,
            EvidenceLevel.COMPLETE_REFERENCE,
            True,
            (),
            FinalStatus.PRODUCTION_PASS,
        ),
        (
            True,
            EvidenceLevel.COMPLETE_REFERENCE,
            False,
            (),
            FinalStatus.PRODUCTION_FAIL,
        ),
        (
            True,
            EvidenceLevel.HUMAN_CONSTRAINT,
            None,
            (ConstraintResult("web_quantity", ConstraintStatus.FAIL, "多拆一块"),),
            FinalStatus.PRODUCTION_FAIL,
        ),
        (
            True,
            EvidenceLevel.HUMAN_CONSTRAINT,
            None,
            (ConstraintResult("web_quantity", ConstraintStatus.PASS, "数量正确"),),
            FinalStatus.EVIDENCE_INSUFFICIENT,
        ),
        (
            True,
            EvidenceLevel.INTERNAL_DIAGNOSTIC,
            None,
            (),
            FinalStatus.EVIDENCE_INSUFFICIENT,
        ),
        (
            False,
            EvidenceLevel.COMPLETE_REFERENCE,
            None,
            (),
            FinalStatus.NO_OUTPUT,
        ),
    ),
)
def test_final_status_is_decided_by_external_evidence_not_internal_acceptance(
    output_available: bool,
    evidence_level: EvidenceLevel,
    reference_passed: bool | None,
    constraints: tuple[ConstraintResult, ...],
    expected: FinalStatus,
) -> None:
    assert (
        classify_verdict(
            output_available=output_available,
            evidence_level=evidence_level,
            complete_reference_passed=reference_passed,
            constraint_results=constraints,
            internal_disposition="auto_accept",
        )
        is expected
    )


def test_build_contracts_joins_96_originals_into_68_18_and_10(
    tmp_path: Path,
) -> None:
    root = _sample_tree(tmp_path / "corpus")

    contracts = build_sample_contracts(
        root,
        classification_manifest=_classification_manifest(root),
    )

    assert len(contracts) == 96
    assert sum(
        item.evidence_level is EvidenceLevel.HUMAN_CONSTRAINT
        for item in contracts
    ) == 68
    assert sum(
        item.evidence_level is EvidenceLevel.COMPLETE_REFERENCE
        for item in contracts
    ) == 18
    assert sum(
        item.evidence_level is EvidenceLevel.INTERNAL_DIAGNOSTIC
        for item in contracts
    ) == 10
    assert {item.sample_id for item in contracts} == {
        f"sample-{number:02d}" for number in range(96)
    }


def test_contracts_freeze_bh_box_family_from_complete_classification_manifest(
    tmp_path: Path,
) -> None:
    root = _sample_tree(tmp_path / "corpus")
    manifest = _classification_manifest(root, bh_ids={"sample-00", "sample-01"})

    contracts = build_sample_contracts(root, classification_manifest=manifest)

    family_by_id = {contract.sample_id: contract.family for contract in contracts}
    assert family_by_id["sample-00"] == "BH"
    assert family_by_id["sample-01"] == "BH"
    assert set(family_by_id.values()) == {"BH", "BOX"}


def test_contracts_require_a_complete_frozen_classification_manifest(
    tmp_path: Path,
) -> None:
    root = _sample_tree(tmp_path / "corpus")

    with pytest.raises(ValueError, match="分类清单"):
        build_sample_contracts(root)


def test_compound_first_sheet_categories_keep_every_explicit_constraint(
    tmp_path: Path,
) -> None:
    root = _sample_tree(tmp_path / "corpus")
    contracts = build_sample_contracts(
        root,
        classification_manifest=_classification_manifest(root),
    )
    by_category = {item.category: item for item in contracts if item.category}

    assert {item.key for item in by_category["01_上下翼缘板拆反且上翼缘长度错误"].constraints} == {
        "flange_role_order",
        "flange_length",
    }
    assert {item.key for item in by_category["02_腹板多拆且翼缘板长度错误"].constraints} == {
        "web_quantity",
        "flange_length",
    }
    assert {item.key for item in by_category["05_相同腹板和翼缘板被重复拆分"].constraints} == {
        "web_deduplication",
        "flange_deduplication",
    }


def test_human_wording_adds_constraints_omitted_by_the_category_name(
    tmp_path: Path,
) -> None:
    root = _sample_tree(tmp_path / "corpus")
    analysis = next(
        (root / "02_第一张合并图_问题样本" / "04_相同腹板被重复拆分").glob(
            "*/错误分析.txt"
        )
    )
    analysis.write_text(
        analysis.read_text(encoding="utf-8").replace(
            "人工原始表述：人工确认的生产错误",
            "人工原始表述：腹板一样，不需要拆开，上翼缘长度错误",
        ),
        encoding="utf-8",
    )

    contract = next(
        item
        for item in build_sample_contracts(
            root,
            classification_manifest=_classification_manifest(root),
        )
        if item.sample_id == analysis.parent.name
    )

    assert {item.key for item in contract.constraints} == {
        "web_deduplication",
        "flange_length",
    }


def test_contract_snapshot_covers_every_evidence_file_without_absolute_paths(
    tmp_path: Path,
) -> None:
    root = _sample_tree(tmp_path / "corpus")
    contracts = build_sample_contracts(
        root,
        classification_manifest=_classification_manifest(root),
    )

    snapshot = source_snapshot(contracts)

    assert len(snapshot) == 96 + 68 * 3 + 18 * 4
    assert all(not Path(relative).is_absolute() for relative in snapshot)
    assert all(len(digest) == 64 for digest in snapshot.values())


def test_contract_manifest_exposes_frozen_reference_and_historical_result_dwgs(
    tmp_path: Path,
) -> None:
    root = _sample_tree(tmp_path / "corpus")

    classification = _classification_manifest(root)
    manifest = build_contract_manifest(
        root,
        classification_manifest=classification,
    )

    assert manifest["schema"] == "BOX-EXTERNAL-ACCEPTANCE-CONTRACTS-1.0"
    assert manifest["sample_count"] == 96
    assert manifest["counts"] == {
        "complete_reference": 18,
        "human_constraint": 68,
        "internal_diagnostic": 10,
    }
    references = [
        sample["complete_reference"]
        for sample in manifest["samples"]
        if sample["complete_reference"] is not None
    ]
    assert len(references) == 18
    assert all(item["relative_path"].endswith(".dwg") for item in references)
    assert all(len(item["sha256"]) == 64 for item in references)
    historical_results = [
        sample["historical_wrong_result"]
        for sample in manifest["samples"]
        if sample["historical_wrong_result"] is not None
    ]
    assert len(historical_results) == 86
    assert all(
        Path(item["relative_path"]).name.startswith("02_程序错误结果")
        for item in historical_results
    )
    assert all(len(item["sha256"]) == 64 for item in historical_results)
    assert manifest["source_snapshot"] == source_snapshot(
        build_sample_contracts(root, classification_manifest=classification)
    )


def test_contract_manifest_writer_uses_utf8_lf_without_crlf(tmp_path: Path) -> None:
    root = _sample_tree(tmp_path / "corpus")
    output = tmp_path / "reports" / "contracts.json"

    write_contract_manifest(
        root,
        output,
        classification_manifest=_classification_manifest(root),
    )

    payload = output.read_bytes()
    assert payload.endswith(b"\n")
    assert b"\r\n" not in payload
    assert "人工确认的生产错误".encode() in payload


def test_duplicate_problem_sample_id_is_rejected(tmp_path: Path) -> None:
    root = _sample_tree(tmp_path / "corpus")
    duplicate = (
        root
        / "03_第二张合并图_问题样本"
        / "01_相同腹板未去重_不同翼缘被误合并"
        / "sample-00"
    )
    _write(duplicate / "01_原图_sample-00.dxf", "duplicate")
    _write(duplicate / "02_程序错误结果_白色_sample-00.dwg", "wrong")
    _write(duplicate / "03_正确结果_黄色_sample-00.dwg", "correct")
    _write(duplicate / "错误分析.txt", "编号：sample-00")

    with pytest.raises(ValueError, match="重复问题样本"):
        build_sample_contracts(root)


def test_problem_sample_without_matching_original_is_rejected(tmp_path: Path) -> None:
    root = _sample_tree(tmp_path / "corpus")
    (root / "01_全部原文件" / "sample-00.dxf").unlink()

    with pytest.raises(ValueError, match="问题样本缺少全部原文件"):
        build_sample_contracts(root)
