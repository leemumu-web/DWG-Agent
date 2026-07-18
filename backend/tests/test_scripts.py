from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def test_cad_benchmark_cli_contract_and_concurrency_parser(tmp_path):
    script = PROJECT_ROOT / "scripts/benchmark_cad_conversion.py"
    assert script.is_file()

    spec = importlib.util.spec_from_file_location("cad_benchmark", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.parse_concurrency("1,4,4,8") == [1, 4, 8]
    with pytest.raises(ValueError):
        module.parse_concurrency("0,2")

    help_result = subprocess.run(
        ["python", str(script), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_result.returncode == 0
    assert "--direction" in help_result.stdout
    missing_result = subprocess.run(
        [
            "python",
            str(script),
            "--input-dir",
            str(tmp_path / "missing"),
            "--direction",
            "dwg2dxf",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing_result.returncode == 2
    assert "不存在" in missing_result.stderr


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
    # start-all/start-dev use the shared ensure_db_ready() helper which
    # internally calls ``bash scripts/db.sh start`` (and init on first run).
    for path in ("scripts/start-all.sh", "scripts/start-dev.sh"):
        content = _read(path)
        assert "ensure_db_ready" in content
        assert "ensure_service 3306" not in content
    # lib.sh ensure_db_ready must delegate to db.sh:
    lib_content = _read("scripts/lib.sh")
    assert 'scripts/db.sh" start' in lib_content
    assert 'scripts/db.sh" init' in lib_content


def test_start_stop_status_scripts_manage_report_worker():
    # Consolidated helpers: start_all_workers / stop_all_workers / WORKER_SPECS
    for path in ("scripts/start-all.sh", "scripts/start-dev.sh"):
        assert "start_all_workers" in _read(path)

    assert "stop_all_workers" in _read("scripts/stop-all.sh")
    assert "stop_celery_worker" in _read("scripts/lib.sh")
    assert "dwg-agent-${label}.pid" in _read("scripts/lib.sh")

    lib_content = _read("scripts/lib.sh")
    assert "WORKER_SPECS" in lib_content
    # Every queue/slug must appear in WORKER_SPECS (defined in lib.sh, iterated
    # by start_all_workers / stop_all_workers / status.sh).
    for label in ("report", "dxf", "dxf2dwg", "dxf2excel", "excel-final"):
        assert label in lib_content

    status_content = _read("scripts/status.sh")
    assert "celery_worker_pids" in status_content
    assert "WORKER_SPECS" in status_content


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
    assert "WORKER_SPECS" in lib_content
    for queue, slug in expected.items():
        assert f'"{queue}|' in lib_content
        assert all("start_all_workers" in content for content in start_contents)
        assert f"{queue}" in lib_content
        assert slug in lib_content
    assert "stop_all_workers" in stop_content


def test_cad_worker_wrapper_owns_xvfb_and_celery_lifecycle():
    wrapper = PROJECT_ROOT / "scripts/run-cad-worker.sh"

    assert wrapper.is_file()
    content = wrapper.read_text(encoding="utf-8")
    assert "Xvfb" in content
    assert "trap cleanup" in content
    assert 'export DISPLAY="$display"' in content
    assert "celery_pid" in content
    assert "wait_for_x_socket" in content
    assert 'pid_file="/tmp/dwg-celery-${queue}.pid"' in content
    assert 'rm -f "$pid_file"' in content
    assert 'echo "$celery_pid" >"$pid_file"' in content
    assert 'display_lock="/tmp/.X${display_number}-lock"' in content
    assert 'kill -0 "$display_owner"' in content
    assert 'rm -f "$x_socket" "$display_lock"' in content


def test_local_cad_worker_concurrency_and_display_are_configurable():
    content = _read("scripts/lib.sh")

    assert 'DXF_WORKER_CONCURRENCY="${DXF_WORKER_CONCURRENCY:-8}"' in content
    assert 'DXF2DWG_WORKER_CONCURRENCY="${DXF2DWG_WORKER_CONCURRENCY:-8}"' in content
    assert 'DXF_WORKER_DISPLAY="${DXF_WORKER_DISPLAY:-:91}"' in content
    assert 'DXF2DWG_WORKER_DISPLAY="${DXF2DWG_WORKER_DISPLAY:-:92}"' in content
    assert '"dxf|${DXF_WORKER_CONCURRENCY}|dxf|${DXF_WORKER_DISPLAY}"' in content
    assert '"dxf2dwg|${DXF2DWG_WORKER_CONCURRENCY}|dxf2dwg|${DXF2DWG_WORKER_DISPLAY}"' in content


def test_worker_stop_signals_only_celery_parent_processes():
    content = _read("scripts/lib.sh")

    assert "celery_worker_parent_pids" in content
    assert 'kill -TERM "${parent_pids[@]}"' in content
    assert 'pkill -TERM -f "$pattern"' not in content


def test_status_warns_when_local_and_compose_consume_the_same_cad_queue():
    content = _read("scripts/status.sh")

    assert "docker compose ps --status running --services" in content
    assert "本地与 Compose 同时消费" in content
    assert 'worker-${label}' in content


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
    assert "stop_all_workers" in stop_content
    assert "inspect" not in lib_content


def test_status_script_uses_side_effect_free_health_probe():
    content = _read("scripts/status.sh")

    assert "http://127.0.0.1:8080/health" in content
    assert "/api/v1/auth/sessions" not in content
    assert "password" not in content.lower()


def test_start_script_does_not_print_bootstrap_password():
    # start-all.sh delegates credential display to print_admin_credentials (lib.sh)
    # which reads SUPER_ADMIN_USERNAME / SUPER_ADMIN_PASSWORD from .env at runtime.
    start_all = _read("scripts/start-all.sh")
    lib_content = _read("scripts/lib.sh")

    assert "SuperAdminPass1" not in start_all
    assert "print_admin_credentials" in start_all
    # lib.sh must reference the env var names (not the actual password)
    assert "SUPER_ADMIN_PASSWORD" in lib_content
    assert "SUPER_ADMIN_USERNAME" in lib_content


def test_background_start_is_stable_and_dev_start_keeps_hot_reload():
    start_all = _read("scripts/start-all.sh")
    start_dev = _read("scripts/start-dev.sh")
    lib_content = _read("scripts/lib.sh")

    assert "--reload" not in start_all
    assert "start_local_backend" in start_all
    assert "nohup setsid" in lib_content
    assert "</dev/null" in lib_content
    assert "--reload" in start_dev


def test_start_all_supports_explicit_owned_backend_restart():
    start_all = _read("scripts/start-all.sh")
    lib_content = _read("scripts/lib.sh")

    assert "--restart-backend" in start_all
    assert "restart_owned_backend" in start_all
    assert "owned_backend_pid" in lib_content
    assert "kill -TERM" in lib_content
    assert "kill -KILL" not in lib_content


def test_runtime_and_frontend_staleness_are_reported():
    lib_content = _read("scripts/lib.sh")
    status_content = _read("scripts/status.sh")
    start_content = _read("scripts/start-all.sh")

    assert "backend_runtime_stale" in lib_content
    assert "frontend_dist_stale" in lib_content
    assert "运行代码已过期" in status_content
    assert "前端构建产物已过期" in start_content
    assert "exit 1" in status_content


def test_files_newer_than_epoch_uses_real_mtimes(tmp_path):
    older = tmp_path / "older.py"
    newer = tmp_path / "newer.py"
    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")
    os.utime(older, (100, 100))
    os.utime(newer, (300, 300))
    command = (
        f'source "{PROJECT_ROOT / "scripts/lib.sh"}"; '
        'files_newer_than_epoch 200 "$1" "$2"'
    )

    stale = subprocess.run(
        ["bash", "-c", command, "bash", str(older), str(newer)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    fresh = subprocess.run(
        ["bash", "-c", command, "bash", str(older)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert stale.returncode == 0
    assert fresh.returncode == 1


def test_doctor_groups_4xx_separates_499_and_redacts_queries(tmp_path):
    access_log = tmp_path / "access.log"
    access_log.write_text(
        "\n".join(
            [
                '127.0.0.1 - - [18/Jul/2026:11:16:14 +0800] "POST /api/v1/files/batches/bulk-delete HTTP/1.1" 405 179 "-" "Chrome" rt=0.002 rid=route-405',
                '127.0.0.1 - - [18/Jul/2026:11:16:16 +0800] "POST /api/v1/files/download-zip?signature=secret-signature HTTP/1.1" 409 248 "-" "Chrome" rt=0.192 rid=zip-409',
                '127.0.0.1 - - [18/Jul/2026:11:16:18 +0800] "POST /api/v1/files?batch_name=private-name HTTP/1.1" 499 0 "-" "Chrome" rt=1.200 rid=upload-499',
                '127.0.0.1 - - [18/Jul/2026:11:16:20 +0800] "GET /api/v1/health HTTP/1.1" 200 10 "-" "Chrome" rt=0.001 rid=ok-200',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts/doctor.sh"), "--log-only"],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "NGINX_ACCESS_LOG": str(access_log),
            "DOCTOR_SINCE_MINUTES": "0",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "HTTP 405" in result.stdout
    assert "HTTP 409" in result.stdout
    assert "客户端断开 (499)" in result.stdout
    assert "/api/v1/files/download-zip" in result.stdout
    assert "route-405" in result.stdout
    assert "zip-409" in result.stdout
    assert "secret-signature" not in result.stdout
    assert "private-name" not in result.stdout


def test_doctor_missing_log_is_unchecked_not_healthy(tmp_path):
    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts/doctor.sh"), "--log-only"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "NGINX_ACCESS_LOG": str(tmp_path / "missing.log")},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "未检查" in result.stdout


def test_verify_script_exposes_quick_full_and_blocked_modes():
    content = _read("scripts/verify.sh")

    assert "quick" in content
    assert "full" in content
    assert "--allow-blocked" in content
    assert "run_gate" in content
    assert "make docs-check" in content
    assert "npm run build" in content


def test_scripts_readme_documents_every_operational_entrypoint():
    content = _read("scripts/README.md")

    for command in (
        "start-all.sh",
        "start-dev.sh",
        "stop-all.sh",
        "status.sh",
        "doctor.sh",
        "verify.sh",
        "db.sh",
        "docker.sh",
        "forward-to-win11.sh",
        "run-cad-worker.sh",
        "reap_storage.py",
    ):
        assert command in content
    assert "--restart-backend" in content
    assert "--allow-blocked" in content


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
