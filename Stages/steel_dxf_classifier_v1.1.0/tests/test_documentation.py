from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_operator_docs_state_input_output_and_fail_closed_contracts() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "README.md",
            ROOT / "docs" / "CLASSIFICATION_RULES.md",
            ROOT / "docs" / "VALIDATION.md",
            ROOT / "docs" / "IO_CONTRACT.md",
        )
    )

    for required in (
        "<项目名称>_dxf",
        "<项目名称>_<零件类型>_dxf",
        "第一层",
        "不递归",
        "待确认",
        "无法读取",
        "--overwrite",
        "截面",
        "材料表",
        "GB2312",
        "ANSI_936",
        "BH",
        "BBH",
        "BOX",
        "XBOX",
        "PL",
        "RHS",
        "JSON",
        "CSV",
        "_拆板前.dxf",
        "原地重命名",
        "重复运行",
        "命名冲突",
        "内容保持不变",
        "--json",
        "STEEL-DXF-CLI-1.1",
        "STEEL-DXF-CLASSIFICATION-1.1",
        "completed_with_review",
        "stdout",
        "stderr",
        "64",
        "1.1.0",
        "--reinstall",
        "移动",
    ):
        assert required in text


def test_release_docs_record_real_project_validation() -> None:
    text = (ROOT / "docs" / "VALIDATION.md").read_text(encoding="utf-8")

    for required in (
        "项目1",
        "72",
        "BH 67",
        "BOX 2",
        "PL 3",
        "项目2",
        "171",
        "BH 141",
        "BOX 30",
        "byte_mismatch=0",
        "待确认 0",
        "无法读取 0",
    ):
        assert required in text
