from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.identity.interface import Role, User
from app.modules.operations.control_plane.interface import record_worker_activity
from app.platform.security.tokens import hash_password


def _admin_headers(client: TestClient) -> dict[str, str]:
    init_db()
    login = client.post(
        "/api/v1/auth/sessions", json={"username": "admin", "password": "SuperAdminPass1"}
    )
    assert login.status_code == 201, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def test_control_plane_overview_discloses_actual_sql_broker_and_pending_contracts():
    client = TestClient(app)
    response = client.get("/api/v1/control-plane/overview", headers=_admin_headers(client))
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["broker"]["kind"] == "mysql_sqlalchemy"
    assert payload["broker"]["ready_count_source"] == "unavailable"
    assert payload["implementation"]["rabbitmq"] == "pending"
    assert {item["name"] for item in payload["queues"]} >= {"dispatch", "maintenance"}
    modes = {item["name"]: item["mode"] for item in payload["queues"]}
    assert modes["maintenance"] == "active"
    assert {name for name, mode in modes.items() if mode == "contract_only"} == {
        "agent",
        "cad",
        "dispatch",
    }


def test_worker_activity_persists_event_and_stale_state(db):
    record_worker_activity(
        db,
        worker_name="dispatch@test",
        status="online",
        event_type="worker.online",
        queues=["dispatch"],
        concurrency=1,
    )
    db.commit()
    client = TestClient(app)
    headers = _admin_headers(client)
    events = client.get("/api/v1/control-plane/events", headers=headers)
    assert events.status_code == 200, events.text
    assert events.json()["pagination"]["total"] >= 1
    overview = client.get("/api/v1/control-plane/overview", headers=headers).json()["data"]
    worker = next(item for item in overview["workers"] if item["worker_name"] == "dispatch@test")
    assert worker["status"] == "online"
    assert worker["queues"] == ["dispatch"]


def test_control_plane_requires_privileged_role():
    response = TestClient(app).get("/api/v1/control-plane/overview")
    assert response.status_code == 401


def test_windows_node_contract_is_explicitly_pending():
    client = TestClient(app)
    response = client.get(
        "/api/v1/control-plane/contracts/windows-node-agent", headers=_admin_headers(client)
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "pending"


def test_admin_can_queue_bounded_stale_job_recovery():
    client = TestClient(app)
    response = client.post(
        "/api/v1/control-plane/maintenance/reconcile-stale-jobs",
        headers=_admin_headers(client),
    )
    assert response.status_code == 202, response.text
    payload = response.json()["data"]
    assert payload["queue"] == "maintenance"
    assert payload["operation"] == "reconcile_stale_jobs"


def test_auditor_cannot_queue_maintenance_work(db):
    client = TestClient(app)
    init_db()
    auditor = User(
        username="auditor", password_hash=hash_password("AuditorPass1234"), real_name="Auditor"
    )
    auditor.roles.append(db.scalar(select(Role).where(Role.code == "auditor")))
    db.add(auditor)
    db.commit()
    login = client.post(
        "/api/v1/auth/sessions", json={"username": "auditor", "password": "AuditorPass1234"}
    )
    response = client.post(
        "/api/v1/control-plane/maintenance/reconcile-stale-jobs",
        headers={"Authorization": f"Bearer {login.json()['data']['access_token']}"},
    )
    assert response.status_code == 403, response.text
