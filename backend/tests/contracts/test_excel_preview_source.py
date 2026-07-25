from tests.support.paths import REPO_ROOT


def test_excel_preview_uses_only_server_backed_fast_preview():
    component = (
        REPO_ROOT / "frontend/src/features/excel-processing/ExcelPreview.tsx"
    ).read_text(encoding="utf-8")
    model = (
        REPO_ROOT
        / "frontend/src/features/excel-processing/model/excelPreviewModel.tsx"
    ).read_text(encoding="utf-8")

    assert "fetchExcelPreview" in component
    assert "downloadFile" in component
    for removed in (
        "增强预览",
        "LuckyExcel",
        "getFileDownloadUrl",
        "apiClient",
        "loadEnhanced",
        "type PreviewMode",
        "useState<PreviewMode>",
    ):
        assert removed not in component
    assert "Lucky" not in model
    assert not (REPO_ROOT / "frontend/public/luckyexcel.umd.js").exists()
