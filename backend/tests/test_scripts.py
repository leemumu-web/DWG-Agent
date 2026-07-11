from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def test_database_script_provides_mysql_runtime_entrypoints():
    db_script = PROJECT_ROOT / "scripts/db.sh"

    assert db_script.exists()
    content = db_script.read_text(encoding="utf-8")
    for command in (
        "start",
        "setup-user",
        "init",
        "migrate",
        "migration-test",
        "check",
        "status",
        "shell",
        "logs",
    ):
        assert f'"{command}")' in content
    assert "DATABASE_URL" in content
    assert "mysql+pymysql" in content
    assert "SQLite" in content


def test_database_script_runs_alembic_and_checks_timestamp_schema():
    content = _read("scripts/db.sh")

    assert "uv run alembic upgrade head" in content
    for table in (
        "project_members",
        "drawing_versions",
        "review_records",
        "agent_run_steps",
    ):
        assert table in content
    assert "created_at" in content
    assert "updated_at" in content


def test_database_script_exposes_isolated_mysql_migration_test():
    content = _read("scripts/db.sh")

    assert '"migration-test")' in content
    assert "dwg_agent_migration_test_" in content
    assert "CREATE DATABASE" in content
    assert "DROP DATABASE IF EXISTS" in content
    assert "uv run alembic upgrade head" in content


def test_start_scripts_delegate_database_startup_to_db_script():
    for path in ("scripts/start-all.sh", "scripts/start-dev.sh"):
        content = _read(path)
        assert 'scripts/db.sh" start' in content
        assert 'scripts/db.sh" init' in content
        assert "ensure_service 3306" not in content


def test_start_stop_status_scripts_manage_report_worker():
    for path in ("scripts/start-all.sh", "scripts/start-dev.sh"):
        assert "start_report_worker" in _read(path)

    assert "stop_celery_worker report report" in _read("scripts/stop-all.sh")
    assert "dwg-agent-${label}.pid" in _read("scripts/lib.sh")

    status_content = _read("scripts/status.sh")
    assert "celery_worker_pids" in status_content
    for label in ("report", "dxf", "dxf2dwg", "dxf2excel", "excel-final"):
        assert label in status_content


def test_local_scripts_manage_every_implemented_pipeline_worker():
    lib_content = _read("scripts/lib.sh")
    start_contents = [_read("scripts/start-all.sh"), _read("scripts/start-dev.sh")]
    stop_content = _read("scripts/stop-all.sh")

    expected = {
        "dxf": "dxf",
        "dxf2dwg": "dxf2dwg",
        "dxf2excel": "dxf2excel",
        "excel_final": "excel-final",
    }
    assert "start_celery_worker" in lib_content
    for queue, slug in expected.items():
        function_name = f"start_{queue}_worker"
        assert function_name in lib_content
        assert all(function_name in content for content in start_contents)
        assert f"stop_celery_worker {queue} {slug}" in stop_content


def test_stop_all_does_not_kill_unowned_backend_port():
    content = _read("scripts/stop-all.sh")

    assert "fuser -k" not in content
    assert "端口 ${LOCAL_BACKEND_PORT} 仍被占用" in content
    assert 'port_free "$LOCAL_BACKEND_PORT"' in content


def test_worker_lifecycle_detects_orphaned_pidfiles_and_duplicate_consumers():
    lib_content = _read("scripts/lib.sh")
    stop_content = _read("scripts/stop-all.sh")

    assert "celery_worker_pids" in lib_content
    assert "stop_celery_worker" in lib_content
    assert 'pgrep -f "$pattern"' in lib_content
    assert "已存在但 pidfile 缺失" in lib_content
    assert "stop_celery_worker report report" in stop_content
    assert "inspect" not in lib_content


def test_status_script_uses_side_effect_free_health_probe():
    content = _read("scripts/status.sh")

    assert "http://127.0.0.1:8080/health" in content
    assert "/api/v1/auth/sessions" not in content
    assert "password" not in content.lower()


def test_start_script_does_not_print_bootstrap_password():
    content = _read("scripts/start-all.sh")

    assert "SuperAdminPass1" not in content
    assert "SUPER_ADMIN_PASSWORD" in content


def test_background_start_is_stable_and_dev_start_keeps_hot_reload():
    start_all = _read("scripts/start-all.sh")
    start_dev = _read("scripts/start-dev.sh")

    assert "--reload" not in start_all
    assert "nohup setsid" in start_all
    assert "</dev/null" in start_all
    assert "--reload" in start_dev


def test_nginx_proxies_fastapi_documentation_routes():
    for relative_path in ("infra/nginx/nginx.local.conf", "infra/nginx/nginx.conf"):
        content = _read(relative_path)
        assert "location = /openapi.json" in content
        assert "location ~ ^/(docs|redoc)(/.*)?$" in content
        assert content.count("proxy_pass http://backend;") >= 5


def test_makefile_exposes_database_script_targets():
    content = _read("Makefile")

    for target in (
        "db-start:",
        "db-setup:",
        "db-init:",
        "db-migrate:",
        "db-migration-test:",
        "db-status:",
        "db-shell:",
    ):
        assert target in content


def test_db_script_rejects_sqlite_runtime_url(tmp_path):
    env_content = "\n".join(
        [
            "DATABASE_URL=sqlite:///./var/app.db",
            "MYSQL_HOST=127.0.0.1",
            "MYSQL_PORT=3306",
            "MYSQL_DATABASE=dwg_agent",
            "MYSQL_USER=dwg_user",
            "MYSQL_PASSWORD=test-password",
            "",
        ]
    )
    root_env = tmp_path / ".env"
    backend_env = tmp_path / "backend.env"
    root_env.write_text(env_content, encoding="utf-8")
    backend_env.write_text(env_content, encoding="utf-8")

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts/db.sh"), "check"],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "DWG_ROOT_ENV_FILE": str(root_env),
            "DWG_ENV_FILE": str(backend_env),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "运行入口已优雅退出 SQLite" in result.stdout
