"""HTTP-layer tests for the workflow API.

Tests every route with user-scoped access, invalid payloads, and
state-machine boundaries exercised through the TestClient."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap.seed import init_db
from app.main import app


def _client() -> TestClient:
    init_db()
    return TestClient(app, raise_server_exceptions=False)


def _admin_headers(client: TestClient) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def _engineer_user(
    client: TestClient, admin_h: dict[str, str], prefix: str = "eng"
) -> tuple[int, dict[str, str]]:
    username = f"{prefix}-{uuid4().hex[:8]}"
    pwd = "EngPassword1234"
    created = client.post(
        "/api/v1/users",
        headers=admin_h,
        json={"username": username, "password": pwd, "real_name": f"Eng {prefix}"},
    )
    assert created.status_code == 201, created.text
    uid = created.json()["data"]["id"]
    role_resp = client.post(
        f"/api/v1/users/{uid}/roles", headers=admin_h, json={"role_code": "engineer"}
    )
    assert role_resp.status_code == 201, role_resp.text
    login = client.post("/api/v1/auth/sessions", json={"username": username, "password": pwd})
    assert login.status_code == 201, login.text
    return uid, {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def _project(client: TestClient, owner_h: dict[str, str]) -> int:
    code = f"WFAPI-{uuid4().hex[:6]}"
    resp = client.post(
        "/api/v1/projects",
        headers=owner_h,
        json={"code": code, "name": f"API Test {code}", "description": "test"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _add_member(
    client: TestClient, project_id: int, user_id: int, role: str, admin_h: dict[str, str]
) -> None:
    resp = client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=admin_h,
        json={"user_id": user_id, "project_role": role},
    )
    assert resp.status_code == 201, resp.text


# ── create workflow ──────────────────────────────────────────────────────────


def test_create_workflow_returns_201_and_stages():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "wf-create")
    pid = _project(c, owner_h)
    resp = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "Integration WF", "workflow_type": "excel_delivery"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["name"] == "Integration WF"
    assert data["status"] == "draft"
    assert len(data["stages"]) == 4
    assert len(data["artifacts"]) == 0


def test_create_workflow_missing_project_rejected():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "ghost-proj")
    resp = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": 99999, "name": "Ghost", "workflow_type": "file_delivery"},
    )
    assert resp.status_code >= 400, resp.text


def test_create_workflow_blank_name_rejected():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "blank-nm")
    pid = _project(c, owner_h)
    for name in ("", "   "):
        resp = c.post(
            "/api/v1/workflows",
            headers=owner_h,
            json={"project_id": pid, "name": name, "workflow_type": "file_delivery"},
        )
        assert resp.status_code >= 400, resp.text


def test_create_workflow_bad_type_rejected():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "bad-typ")
    pid = _project(c, owner_h)
    resp = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "Bad", "workflow_type": "ai_pipeline"},
    )
    assert resp.status_code >= 400, resp.text


def test_non_member_cannot_create_workflow():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "owner-cr")
    _, stranger_h = _engineer_user(c, admin_h, "str-cr")
    pid = _project(c, owner_h)
    resp = c.post(
        "/api/v1/workflows",
        headers=stranger_h,
        json={"project_id": pid, "name": "Stranger WF", "workflow_type": "file_delivery"},
    )
    assert resp.status_code == 403, resp.text


# ── list workflows ───────────────────────────────────────────────────────────


def test_list_workflows_shows_only_accessible():
    c = _client()
    admin_h = _admin_headers(c)
    oid_a, owner_a = _engineer_user(c, admin_h, "la")
    oid_b, owner_b = _engineer_user(c, admin_h, "lb")
    oid_c, _ = _engineer_user(c, admin_h, "lc")
    pa = _project(c, owner_a)
    pb = _project(c, owner_b)
    _add_member(c, pb, oid_c, "project_viewer", admin_h)
    c.post(
        "/api/v1/workflows",
        headers=owner_a,
        json={"project_id": pa, "name": "WF A", "workflow_type": "file_delivery"},
    )
    c.post(
        "/api/v1/workflows",
        headers=owner_b,
        json={"project_id": pb, "name": "WF B", "workflow_type": "file_delivery"},
    )
    a_ids = {item["id"] for item in c.get("/api/v1/workflows", headers=owner_a).json()["data"]}
    b_ids = {item["id"] for item in c.get("/api/v1/workflows", headers=owner_b).json()["data"]}
    admin_ids = {item["id"] for item in c.get("/api/v1/workflows", headers=admin_h).json()["data"]}
    assert len(a_ids) >= 1
    assert len(b_ids) >= 1
    assert admin_ids.intersection(a_ids) == a_ids
    assert admin_ids.intersection(b_ids) == b_ids


def test_list_workflows_paginated():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "lp")
    pid = _project(c, owner_h)
    for idx in range(5):
        c.post(
            "/api/v1/workflows",
            headers=owner_h,
            json={"project_id": pid, "name": f"Paginated {idx}", "workflow_type": "file_delivery"},
        )
    p1 = c.get("/api/v1/workflows", headers=owner_h, params={"page": 1, "page_size": 2})
    assert p1.status_code == 200
    assert len(p1.json()["data"]) == 2
    assert p1.json()["pagination"]["total"] >= 5


def test_list_workflows_filter_by_status():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "lfs")
    pid = _project(c, owner_h)
    c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "Draft WF", "workflow_type": "file_delivery"},
    )
    resp = c.get("/api/v1/workflows", headers=owner_h, params={"status": "draft"})
    assert resp.status_code == 200
    assert len(resp.json()["data"]) >= 1
    empty = c.get("/api/v1/workflows", headers=owner_h, params={"status": "succeeded"})
    assert empty.json()["data"] == []


def test_list_workflows_bad_status_rejected():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "lbs")
    resp = c.get("/api/v1/workflows", headers=owner_h, params={"status": "flying"})
    assert resp.status_code >= 400, resp.text


# ── get workflow detail ──────────────────────────────────────────────────────


def test_get_workflow_returns_detail():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "gd")
    pid = _project(c, owner_h)
    created = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "Detail WF", "workflow_type": "file_delivery"},
    )
    wf_id = created.json()["data"]["id"]
    resp = c.get(f"/api/v1/workflows/{wf_id}", headers=owner_h)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "stages" in data
    assert "artifacts" in data
    assert len(data["stages"]) == 3


def test_get_workflow_non_member_rejected():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "gd-owner")
    _, stranger_h = _engineer_user(c, admin_h, "gd-str")
    pid = _project(c, owner_h)
    created = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "Hidden WF", "workflow_type": "file_delivery"},
    )
    wf_id = created.json()["data"]["id"]
    resp = c.get(f"/api/v1/workflows/{wf_id}", headers=stranger_h)
    assert resp.status_code == 403, resp.text


def test_get_nonexistent_workflow_returns_404():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "g404")
    resp = c.get("/api/v1/workflows/99999", headers=owner_h)
    assert resp.status_code == 404, resp.text


# ── start workflow ───────────────────────────────────────────────────────────


def test_start_workflow_transitions():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "sw")
    pid = _project(c, owner_h)
    created = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "Start Me", "workflow_type": "file_delivery"},
    )
    wf_id = created.json()["data"]["id"]
    resp = c.post(f"/api/v1/workflows/{wf_id}/start", headers=owner_h)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "waiting_input"


def test_cannot_start_twice():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "sw2")
    pid = _project(c, owner_h)
    created = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "Double Start", "workflow_type": "file_delivery"},
    )
    wf_id = created.json()["data"]["id"]
    c.post(f"/api/v1/workflows/{wf_id}/start", headers=owner_h)
    resp = c.post(f"/api/v1/workflows/{wf_id}/start", headers=owner_h)
    assert resp.status_code >= 400, resp.text


def test_non_owner_cannot_start():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "sw-own")
    vid, viewer_h = _engineer_user(c, admin_h, "sw-view")
    pid = _project(c, owner_h)
    _add_member(c, pid, vid, "project_viewer", admin_h)
    created = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "View Guard", "workflow_type": "file_delivery"},
    )
    wf_id = created.json()["data"]["id"]
    resp = c.post(f"/api/v1/workflows/{wf_id}/start", headers=viewer_h)
    assert resp.status_code == 403, resp.text


# ── complete stage ───────────────────────────────────────────────────────────


def test_complete_stage_advances():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "cs")
    pid = _project(c, owner_h)
    created = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "Stage Adv", "workflow_type": "file_delivery"},
    )
    wf_id = created.json()["data"]["id"]
    c.post(f"/api/v1/workflows/{wf_id}/start", headers=owner_h)
    stage_code = created.json()["data"]["stages"][0]["stage_code"]
    resp = c.post(f"/api/v1/workflows/{wf_id}/stages/{stage_code}/completion", headers=owner_h)
    assert resp.status_code == 200
    assert resp.json()["data"]["stages"][0]["status"] == "succeeded"


def test_complete_unknown_stage_rejected():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "cs-bad")
    pid = _project(c, owner_h)
    created = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "Bad Stage", "workflow_type": "file_delivery"},
    )
    wf_id = created.json()["data"]["id"]
    c.post(f"/api/v1/workflows/{wf_id}/start", headers=owner_h)
    resp = c.post(f"/api/v1/workflows/{wf_id}/stages/ghost/completion", headers=owner_h)
    assert resp.status_code >= 400, resp.text


def test_viewer_cannot_complete_stage():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "cs-own")
    vid, viewer_h = _engineer_user(c, admin_h, "cs-view")
    pid = _project(c, owner_h)
    _add_member(c, pid, vid, "project_viewer", admin_h)
    created = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "View Guard", "workflow_type": "file_delivery"},
    )
    wf_id = created.json()["data"]["id"]
    c.post(f"/api/v1/workflows/{wf_id}/start", headers=owner_h)
    stage_code = created.json()["data"]["stages"][0]["stage_code"]
    resp = c.post(f"/api/v1/workflows/{wf_id}/stages/{stage_code}/completion", headers=viewer_h)
    assert resp.status_code == 403, resp.text


# ── cancel workflow ──────────────────────────────────────────────────────────


def test_cancel_workflow():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "canc")
    pid = _project(c, owner_h)
    created = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "Cancel Me", "workflow_type": "file_delivery"},
    )
    wf_id = created.json()["data"]["id"]
    c.post(f"/api/v1/workflows/{wf_id}/start", headers=owner_h)
    resp = c.post(f"/api/v1/workflows/{wf_id}/cancellation-requests", headers=owner_h)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "cancelled"


def test_cancel_nonexistent_workflow_404():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "c404")
    resp = c.post("/api/v1/workflows/99999/cancellation-requests", headers=owner_h)
    assert resp.status_code == 404, resp.text


def test_cancelled_workflow_still_readable():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "cr")
    pid = _project(c, owner_h)
    created = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "Read after cancel", "workflow_type": "file_delivery"},
    )
    wf_id = created.json()["data"]["id"]
    c.post(f"/api/v1/workflows/{wf_id}/cancellation-requests", headers=owner_h)
    resp = c.get(f"/api/v1/workflows/{wf_id}", headers=owner_h)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "cancelled"


# ── empty state ──────────────────────────────────────────────────────────────


def test_list_empty_with_no_workflows():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "el")
    _project(c, owner_h)
    resp = c.get("/api/v1/workflows", headers=owner_h)
    assert resp.status_code == 200
    assert resp.json()["data"] == []


# ── cancel draft without start ──────────────────────────────────────────────


def test_cancel_draft_workflow_without_start():
    c = _client()
    admin_h = _admin_headers(c)
    _, owner_h = _engineer_user(c, admin_h, "dc")
    pid = _project(c, owner_h)
    created = c.post(
        "/api/v1/workflows",
        headers=owner_h,
        json={"project_id": pid, "name": "Draft Cancel", "workflow_type": "file_delivery"},
    )
    wf_id = created.json()["data"]["id"]
    resp = c.post(f"/api/v1/workflows/{wf_id}/cancellation-requests", headers=owner_h)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "cancelled"
