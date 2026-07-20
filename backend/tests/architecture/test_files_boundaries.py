from __future__ import annotations

import ast
import importlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "backend" / "app"

FILE_TABLES = {
    "file_transfers",
    "files",
    "storage_scan_findings",
    "storage_scan_runs",
}

EXPECTED_ROUTES = [
    (("POST",), "", "upload_file"),
    (("POST",), "/upload-zip", "upload_zip"),
    (("GET",), "", "list_files"),
    (("POST",), "/bulk-delete", "bulk_delete_files"),
    (("GET",), "/batches", "list_batches"),
    (("POST",), "/batches/bulk-delete", "bulk_delete_batches"),
    (("DELETE",), "/batches/{batch_name}", "delete_batch"),
    (("GET",), "/batches/{batch_name}/download-zip", "download_batch_zip"),
    (("GET",), "/{file_id}/excel-preview", "get_excel_preview"),
    (("GET",), "/{file_id}/dxf-preview", "get_dxf_preview"),
    (("GET",), "/{file_id}/dxf-preview/content", "get_dxf_preview_content"),
    (("POST",), "/download-zip/preview", "preview_zip_endpoint"),
    (("POST",), "/download-zip", "download_zip_endpoint"),
    (("GET",), "/{file_id}", "get_file"),
    (("DELETE",), "/{file_id}", "delete_file"),
    (("GET",), "/{file_id}/download-url", "get_download_url"),
    (("GET",), "/{file_id}/download", "download_file"),
]

PUBLIC_FILE_CONTRACT = {
    "ACTIVE_TRANSFER_STATUSES",
    "BatchBulkDeleteRequest",
    "BatchBulkDeleteResult",
    "BulkDeleteRequest",
    "DxfPreviewBoundsRead",
    "DxfPreviewRead",
    "FileRead",
    "FileTransfer",
    "StoredFile",
    "StorageScanFinding",
    "StorageScanRun",
    "TransferSpec",
    "ZipDownloadRequest",
    "ZipUploadResult",
    "build_signed_download_url",
    "build_zip_to_path",
    "clear_storage_backend_cache",
    "complete_transfer_in_transaction",
    "get_local_file_path",
    "get_storage_backend",
    "prepare_generated_file_transfer",
    "prepare_transfer_in_transaction",
    "require_file_read_access",
    "sanitize_filename",
    "save_bytes_as_file",
    "save_path_as_file",
    "save_upload_file",
    "session_factory_for",
    "settle_stream",
    "settle_transfer",
    "storage_health",
    "validate_dwg_header",
    "validate_upload_name",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _flatten_routes(router, prefix: str = "") -> list[tuple[tuple[str, ...], str, str]]:
    flattened: list[tuple[tuple[str, ...], str, str]] = []
    for route in router.routes:
        child = getattr(route, "original_router", None)
        if child is not None:
            child_prefix = getattr(route.include_context, "prefix", "")
            flattened.extend(_flatten_routes(child, f"{prefix}{child_prefix}"))
            continue
        flattened.append(
            (
                tuple(sorted(route.methods or ())),
                f"{prefix}{route.path}",
                route.name,
            )
        )
    return flattened


def test_files_interface_owns_exact_registry_tables() -> None:
    files = importlib.import_module("app.modules.files.interface")

    owned = {
        files.StoredFile.__table__.name,
        files.FileTransfer.__table__.name,
        files.StorageScanRun.__table__.name,
        files.StorageScanFinding.__table__.name,
    }

    assert owned == FILE_TABLES


def test_files_interface_exposes_cross_domain_contract() -> None:
    files = importlib.import_module("app.modules.files.interface")

    assert PUBLIC_FILE_CONTRACT <= set(files.__all__)


def test_files_router_preserves_endpoints_and_prioritizes_static_paths() -> None:
    module = importlib.import_module("app.modules.files.routes.router")

    routes = _flatten_routes(module.router)

    assert routes == EXPECTED_ROUTES
    assert routes.index((("POST",), "/bulk-delete", "bulk_delete_files")) < routes.index(
        (("GET",), "/{file_id}", "get_file")
    )
    assert routes.index(
        (("POST",), "/download-zip/preview", "preview_zip_endpoint")
    ) < routes.index((("GET",), "/{file_id}", "get_file"))


def test_other_modules_use_only_files_interface() -> None:
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(APP_ROOT)
        if relative.parts[:2] == ("modules", "files") or relative.parts[:1] == (
            "bootstrap",
        ):
            continue
        for imported in _imports(path):
            prefix = "app.modules.files"
            if imported.startswith(prefix) and not imported.startswith(f"{prefix}.interface"):
                violations.append(f"{relative} -> {imported}")

    assert violations == []


def test_files_interface_does_not_compose_http_routes() -> None:
    path = APP_ROOT / "modules" / "files" / "interface.py"

    assert not any(
        imported.startswith("app.modules.files.routes") for imported in _imports(path)
    )


def test_legacy_file_registry_files_are_retired() -> None:
    retired = (
        "api/v1/files_api.py",
        "models/file.py",
        "models/file_transfer.py",
        "models/storage_scan.py",
        "schemas/file_schema.py",
        "services/file_service.py",
        "services/file_transfer_service.py",
        "services/storage_service.py",
    )

    assert [path for path in retired if (APP_ROOT / path).exists()] == []
