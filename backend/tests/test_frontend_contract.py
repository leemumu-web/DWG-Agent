from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _frontend_source(path: str) -> str:
    return (REPO_ROOT / "frontend/src" / path).read_text(encoding="utf-8")


def test_frontend_password_change_matches_backend_patch_contract():
    source = _frontend_source("api/auth.api.ts")

    assert "apiClient.patch" in source
    assert "'/api/v1/auth/password'" in source


def test_frontend_keeps_access_token_in_session_storage_only():
    source = _frontend_source("stores/auth.store.ts")

    assert "sessionStorage" in source
    assert "localStorage" not in source


def test_frontend_sse_never_puts_access_token_in_url():
    source = _frontend_source("hooks/useJobEvents.ts")

    assert "?token=" not in source
    assert "encodeURIComponent(token)" not in source
    assert "new EventSource(url, { withCredentials: true })" in source


def test_auth_init_can_restore_session_from_httponly_cookie():
    source = _frontend_source("hooks/useAuthInit.ts")

    assert "setSession(data.access_token, data.user)" in source
    assert "if (!token)" not in source


def test_password_change_immediately_clears_revoked_frontend_session():
    source = _frontend_source("features/profile/ProfilePage.tsx")

    assert "clearSession()" in source
    assert "navigate('/login'" in source


def test_non_idempotent_uploads_are_not_automatically_retried():
    source = _frontend_source("api/files.api.ts")

    assert "retries = 0" in source
    assert "body: form,\n    timeout: 120_000,\n  }, 1)" not in source
    assert "body: form,\n    timeout: 300_000,\n  }, 1)" not in source


def test_excel_final_has_frontend_api_types_route_and_tab():
    api_source = _frontend_source("api/excel-final.api.ts")
    router_source = _frontend_source("app/router.tsx")
    tabs_source = _frontend_source("features/files/FilesLayout.tsx")
    page_source = _frontend_source("features/files/ExcelFinalPage.tsx")
    type_source = _frontend_source("types/excel-final.ts")

    assert "/api/v1/excel-final/upload-and-process" in api_source
    assert "/api/v1/excel-final/batches" in api_source
    assert "/api/v1/excel-final/parts/search" in api_source
    assert 'path="excel-final"' in router_source
    assert "/files/excel-final" in tabs_source
    assert "uploadAndProcessExcel" in page_source
    assert "ExcelFinalBatch" in type_source


def test_frontend_system_health_lists_every_pipeline_flag():
    source = _frontend_source("api/system.api.ts")

    for feature in (
        "dxf_pipeline",
        "dxf2dwg_pipeline",
        "dxf2excel_pipeline",
        "excel_final_pipeline",
    ):
        assert feature in source
