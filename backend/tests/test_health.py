from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


class TestRootHealth:
    def test_health_returns_ok(self):
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "ok"
        # Component details are not exposed on the root health endpoint.
        assert "components" not in data

    def test_health_always_returns_200(self):
        """Health is a lightweight liveness probe — always returns 200."""
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "ok"


class TestReadinessHealth:
    def test_readiness_checks_mysql(self):
        client = TestClient(app)

        response = client.get("/health/ready")

        assert response.status_code == 200
        assert response.json()["data"]["database"]["status"] == "ok"

    def test_readiness_returns_503_when_database_is_unavailable(self, monkeypatch):
        from app import main as main_module

        monkeypatch.setattr(
            main_module,
            "db_health",
            lambda: {"status": "error", "message": "database unavailable"},
        )
        client = TestClient(app)

        response = client.get("/health/ready")

        assert response.status_code == 503
        assert response.json()["data"]["status"] == "error"
        assert response.json()["data"]["database"]["message"] == "Database is unavailable."
