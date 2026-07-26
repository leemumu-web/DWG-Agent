from __future__ import annotations

import importlib.util
import os
import stat
import subprocess

import pytest

from tests.support.paths import REPO_ROOT as PROJECT_ROOT


def _read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def _load_worker_specs(**overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            (
                f'source "{PROJECT_ROOT}/scripts/lib/cad_worker.sh" || exit $?; '
                'printf "%s\\n" "${WORKER_SPECS[@]}"'
            ),
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, **overrides},
        text=True,
        capture_output=True,
        check=False,
    )


def test_classification_worker_autoscales_from_one_to_three():
    result = _load_worker_specs()

    assert result.returncode == 0
    assert "dxf_classification|3|dxf-classification||1|3" in result.stdout


def test_classification_worker_autoscale_accepts_valid_override():
    result = _load_worker_specs(
        DXF_CLASSIFICATION_AUTOSCALE_MIN="2",
        DXF_CLASSIFICATION_AUTOSCALE_MAX="4",
    )

    assert result.returncode == 0
    assert "dxf_classification|4|dxf-classification||2|4" in result.stdout


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [("0", "3"), ("2", "1"), ("one", "3")],
)
def test_classification_worker_autoscale_rejects_invalid_range(minimum, maximum):
    result = _load_worker_specs(
        DXF_CLASSIFICATION_AUTOSCALE_MIN=minimum,
        DXF_CLASSIFICATION_AUTOSCALE_MAX=maximum,
    )

    assert result.returncode != 0
    assert "DXF classification autoscale" in result.stderr


def _write_fake_compose(tmp_path):
    fake = tmp_path / "fake-compose"
    fake.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_COMPOSE_CALLS"
if [[ "$*" == *"config --services"* ]]; then
    printf 'api\\nworker\\n'
elif [[ "$*" == *"ps --all --format"* ]]; then
    case "$FAKE_COMPOSE_SCENARIO" in
        healthy) printf 'api|running|healthy\\nworker|running|healthy\\n' ;;
        restarting) printf 'api|running|healthy\\nworker|restarting|unhealthy\\n' ;;
        starting) printf 'api|running|healthy\\nworker|running|starting\\n' ;;
    esac
elif [[ "$*" == *" logs "* ]]; then
    printf 'worker diagnostic\\n'
else
    printf 'compose status\\n'
fi
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _run_compose_health_case(tmp_path, scenario: str, *, timeout: int):
    fake = _write_fake_compose(tmp_path)
    calls_path = tmp_path / "calls.log"
    env = {
        **os.environ,
        "FAKE_COMPOSE_SCENARIO": scenario,
        "FAKE_COMPOSE_CALLS": str(calls_path),
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f'set -u; source "{PROJECT_ROOT}/scripts/lib/compose.sh"; '
                f'COMPOSE_CMD=("{fake}"); '
                f"compose_wait_for_healthy_services {timeout}"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    calls = calls_path.read_text(encoding="utf-8") if calls_path.exists() else ""
    return result, calls


def test_compose_health_gate_accepts_only_complete_healthy_stack(tmp_path):
    result, calls = _run_compose_health_case(tmp_path, "healthy", timeout=1)

    assert result.returncode == 0
    assert "2 services healthy" in result.stdout
    assert "config --services" in calls
    assert "ps --all --format" in calls


def test_compose_health_gate_fails_fast_and_scopes_logs(tmp_path):
    result, calls = _run_compose_health_case(tmp_path, "restarting", timeout=10)

    assert result.returncode != 0
    assert "worker" in result.stderr
    assert "logs --tail=80 worker" in calls


def test_compose_health_gate_times_out_starting_services(tmp_path):
    result, calls = _run_compose_health_case(tmp_path, "starting", timeout=0)

    assert result.returncode != 0
    assert "startup timed out" in result.stderr
    assert "logs --tail=80 worker" in calls


def test_stable_compose_startup_orders_health_gate_before_smoke():
    content = _read("scripts/lib/compose.sh")

    assert "compose_up_workers()" in content
    body = content[
        content.index("compose_up_workers()")
        : content.index("compose_backup()")
    ]
    assert body.index("compose_wait_for_healthy_services") < body.index("compose_smoke")
    assert "up-workers) compose_up_workers" in content


def _run_compose_storage_probe(tmp_path, *, probe_fails: bool = False):
    env_file = tmp_path / ".env.docker"
    env_file.write_text(
        "\n".join(
            [
                "MYSQL_PASSWORD=mysql-secret",
                "MYSQL_ROOT_PASSWORD=mysql-root-secret",
                "MINIO_ROOT_USER=minio-admin",
                "MINIO_ROOT_PASSWORD=minio-root-secret",
                "JWT_SECRET_KEY=jwt-secret",
                "SUPER_ADMIN_PASSWORD=admin-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    calls_path = tmp_path / "calls.log"
    fake = tmp_path / "fake-compose"
    fake.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_COMPOSE_CALLS"
if [[ "$*" == *"ps --all --format"* ]]; then
    printf 'backend-api|running|healthy\\n'
elif [[ "$*" == *"exec -T backend-api python /app/scripts/storage/verify_transactions.py"* ]]; then
    if [[ "${FAKE_PROBE_FAIL:-0}" == "1" ]]; then
        printf 'probe failed safely\\n' >&2
        exit 17
    fi
    printf '{"storage_backend":"minio","cleanup":"probe objects removed"}\\n'
fi
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f'set -u; source "{PROJECT_ROOT}/scripts/lib/compose.sh"; '
                'DOCKER_ENV_FILE="$1"; COMPOSE_CMD=("$2"); compose_verify_storage'
            ),
            "bash",
            str(env_file),
            str(fake),
        ],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "FAKE_COMPOSE_CALLS": str(calls_path),
            "FAKE_PROBE_FAIL": "1" if probe_fails else "0",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls_path.read_text(encoding="utf-8")


def test_compose_verify_storage_runs_probe_only_in_healthy_backend(tmp_path):
    result, calls = _run_compose_storage_probe(tmp_path)

    assert result.returncode == 0
    assert "ps --all --format" in calls
    assert "exec -T backend-api python /app/scripts/storage/verify_transactions.py" in calls
    assert "storage transaction verification passed" in result.stdout
    for secret in (
        "mysql-secret",
        "mysql-root-secret",
        "minio-root-secret",
        "jwt-secret",
        "admin-secret",
    ):
        assert secret not in result.stdout
        assert secret not in result.stderr


def test_compose_verify_storage_propagates_probe_failure(tmp_path):
    result, calls = _run_compose_storage_probe(tmp_path, probe_fails=True)

    assert result.returncode == 17
    assert "exec -T backend-api python /app/scripts/storage/verify_transactions.py" in calls
    assert "storage transaction verification passed" not in result.stdout


def test_compose_verify_storage_is_a_public_command():
    content = _read("scripts/lib/compose.sh")

    assert "verify-storage) compose_verify_storage" in content
    assert "verify-storage" in content[content.index("compose_usage()") : content.index("compose_die()")]


def test_stable_startup_replaces_all_existing_managed_runtime():
    compose = _read("scripts/lib/compose.sh")
    compose_up_workers = compose[
        compose.index("compose_up_workers()")
        : compose.index("compose_backup()")
    ]
    compose_main = compose[compose.index("compose_main()") :]
    host = _read("scripts/start-all.sh")

    assert "--force-recreate" in compose_up_workers
    assert '"${COMPOSE_CMD[@]}" --profile workers down --remove-orphans' in compose_main
    assert 'bash "$PROJECT_ROOT/scripts/stop-all.sh"' in host
    assert host.index('bash "$PROJECT_ROOT/scripts/stop-all.sh"') < host.index(
        "start_all_workers"
    )
    assert "uv sync --frozen" in host
    assert host.index("npm ci --silent") < host.index("npm run build")


def test_start_all_runs_final_status_gate_before_success_banner():
    content = _read("scripts/start-all.sh")

    status_index = content.index('bash "$PROJECT_ROOT/scripts/status.sh"')
    summary_index = content.index("全栈启动完成")
    assert status_index < summary_index
    assert 'if ! bash "$PROJECT_ROOT/scripts/status.sh"; then' in content
    assert "exit 1" in content[status_index:summary_index]


def test_stable_startup_docs_cover_compose_and_host_health_gates():
    content = _read("scripts/README.md")

    assert "up-workers" in content
    assert "180 秒" in content
    assert "80 行日志" in content
    assert "scripts/status.sh" in content
    assert "全部受管 worker" in content


def test_cad_benchmark_cli_contract_and_concurrency_parser(tmp_path):
    script = PROJECT_ROOT / "scripts/cad/benchmark_conversion.py"
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
    facade = db_script.read_text(encoding="utf-8")
    content = _read("scripts/lib/database.sh")
    assert "lib/database.sh" in facade
    assert 'db_main "$@"' in facade
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
    content = _read("scripts/lib/database.sh")

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
    content = _read("scripts/lib/database.sh")

    assert '"migration-test")' in content
    assert "dwg_agent_migration_test_" in content
    assert "CREATE DATABASE" in content
    assert "DROP DATABASE IF EXISTS" in content
    assert "uv run alembic upgrade head" in content
    assert "ScriptDirectory.from_config" in content
    assert "repository must have exactly one Alembic head" in content
    assert 'version != "e2f4b8c6a130"' not in content
    assert "cleanup_migration_test_database" in content
    assert "information_schema.SCHEMATA" in content
    assert "FROM mysql.db" in content
    assert "REVOKE ALL PRIVILEGES ON" in content


def test_infrastructure_verifier_does_not_require_root_for_mysql_evidence():
    content = _read("infra/verification/verify.sh")

    assert "sudo -n mariadb" in content
    assert "bash scripts/db.sh check" in content
    assert "MYSQL_APPLICATION_AVAILABLE" in content
    assert "application path was verified" in content
    assert '"worker-remnant-convert": "remnant_convert"' in content
    assert '"worker-remnant-parse": "remnant_parse"' in content
    assert 'ALL_CHECKS_PASSED:{len(svcs)}' in content
    assert '${COMPOSE_SERVICE_COUNT} services' in content


def test_start_scripts_delegate_database_startup_to_db_script():
    # start-all/start-dev use the shared ensure_db_ready() helper which
    # internally calls ``bash scripts/db.sh start`` (and init on first run).
    for path in ("scripts/start-all.sh", "scripts/start-dev.sh"):
        content = _read(path)
        assert "ensure_db_ready" in content
        assert "ensure_service 3306" not in content
    # The classified database implementation delegates to the stable facade.
    database_content = _read("scripts/lib/database.sh")
    assert 'scripts/db.sh" start' in database_content
    assert 'scripts/db.sh" init' in database_content


def test_start_stop_status_scripts_manage_report_worker():
    # Consolidated helpers: start_all_workers / stop_all_workers / WORKER_SPECS
    for path in ("scripts/start-all.sh", "scripts/start-dev.sh"):
        assert "start_all_workers" in _read(path)

    assert "stop_all_workers" in _read("scripts/stop-all.sh")
    assert "stop_celery_worker" in _read("scripts/lib/cad_worker.sh")
    assert "dwg-agent-${label}.pid" in _read("scripts/lib/cad_worker.sh")

    lib_content = _read("scripts/lib/cad_worker.sh")
    assert "WORKER_SPECS" in lib_content
    # Every queue/slug must appear in WORKER_SPECS (defined in lib.sh, iterated
    # by start_all_workers / stop_all_workers / status.sh).
    for label in (
        "report",
        "dxf-classification",
        "dxf-split",
        "dxf",
        "dxf2dwg",
        "dxf2excel",
        "excel-final",
    ):
        assert label in lib_content

    status_content = _read("scripts/status.sh")
    assert "celery_worker_pids" in status_content
    assert "WORKER_SPECS" in status_content


def test_local_scripts_manage_every_implemented_pipeline_worker():
    lib_content = _read("scripts/lib/cad_worker.sh")
    start_contents = [_read("scripts/start-all.sh"), _read("scripts/start-dev.sh")]
    stop_content = _read("scripts/stop-all.sh")

    expected = {
        "dxf_classification": "dxf-classification",
        "dxf_split": "dxf-split",
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


def test_database_backup_uses_a_dump_client_not_the_interactive_client():
    content = _read("scripts/lib/database.sh")
    backup_section = content[content.index("backup_cmd()") : content.index("restore_cmd()")]

    assert "pick_mysql_dump_client" in content
    assert "mariadb-dump" in content
    assert "mysqldump" in content
    assert '"$MYSQL_DUMP_CLIENT"' in backup_section
    assert '"$MYSQL_CLIENT" -h' not in backup_section


def test_cad_worker_wrapper_owns_xvfb_and_celery_lifecycle():
    wrapper = PROJECT_ROOT / "scripts/run-cad-worker.sh"

    assert wrapper.is_file()
    facade = wrapper.read_text(encoding="utf-8")
    content = _read("scripts/lib/cad_worker.sh")
    assert "cad_worker_main" in facade
    assert "Xvfb" in content
    assert "trap cad_worker_cleanup" in content
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
    content = _read("scripts/lib/cad_worker.sh")

    assert 'DXF_WORKER_CONCURRENCY="${DXF_WORKER_CONCURRENCY:-8}"' in content
    assert 'DXF2DWG_WORKER_CONCURRENCY="${DXF2DWG_WORKER_CONCURRENCY:-8}"' in content
    assert 'DXF_WORKER_DISPLAY="${DXF_WORKER_DISPLAY:-:91}"' in content
    assert 'DXF2DWG_WORKER_DISPLAY="${DXF2DWG_WORKER_DISPLAY:-:92}"' in content
    assert '"dxf|${DXF_WORKER_CONCURRENCY}|dxf|${DXF_WORKER_DISPLAY}"' in content
    assert '"dxf2dwg|${DXF2DWG_WORKER_CONCURRENCY}|dxf2dwg|${DXF2DWG_WORKER_DISPLAY}"' in content


def test_worker_stop_signals_only_celery_parent_processes():
    content = _read("scripts/lib/cad_worker.sh")

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
    assert "stop_owned_backend" in content
    assert "端口 ${LOCAL_BACKEND_PORT} 仍被占用" in content
    assert 'port_free "$LOCAL_BACKEND_PORT"' in content


def test_local_nginx_lifecycle_does_not_require_root_privileges():
    start_content = _read("scripts/start-all.sh")
    stop_content = _read("scripts/stop-all.sh")

    assert "sudo nginx" not in start_content
    assert "sudo nginx" not in stop_content
    assert "NGINX_CLIENT_BODY_DIR" in start_content
    assert 'mkdir -p "$NGINX_CLIENT_BODY_DIR"' in start_content


def test_backend_staleness_ignores_generated_python_bytecode():
    content = _read("scripts/lib/local_stack.sh")

    assert "__pycache__" in content
    assert "*.pyc" in content
    assert "*.pyo" in content


def test_worker_lifecycle_detects_orphaned_pidfiles_and_duplicate_consumers():
    lib_content = _read("scripts/lib/cad_worker.sh")
    stop_content = _read("scripts/stop-all.sh")

    assert "celery_worker_pids" in lib_content
    assert "stop_celery_worker" in lib_content
    assert 'pgrep -f "$pattern"' in lib_content
    assert "已存在但 pidfile 缺失" in lib_content
    assert "worker_pid_is_owned" in lib_content
    assert "旧版或不兼容 Worker" in lib_content
    assert "-A app.platform.messaging.celery_app:celery_app worker" in lib_content
    assert "stop_all_workers" in stop_content
    assert "inspect" not in lib_content


def test_status_script_uses_side_effect_free_health_probe():
    content = _read("scripts/status.sh")

    assert "http://127.0.0.1:8080/health" in content
    assert "/api/v1/auth/sessions" not in content
    assert "password" not in content.lower()


def test_start_script_does_not_print_bootstrap_password():
    # start-all.sh may identify the configured account but must never print or
    # even read the secret into a shell variable just to render its summary.
    start_all = _read("scripts/start-all.sh")
    lib_content = _read("scripts/lib/common.sh")

    assert "SuperAdminPass1" not in start_all
    assert "print_admin_credentials" in start_all
    # The common library references names only, never the actual password.
    assert "SUPER_ADMIN_PASSWORD" in lib_content
    assert "SUPER_ADMIN_USERNAME" in lib_content
    assert 'pass="$(env_value' not in lib_content
    assert "管理员密码:" not in lib_content
    assert "不会在终端显示" in lib_content


def test_background_start_is_stable_and_dev_start_keeps_hot_reload():
    start_all = _read("scripts/start-all.sh")
    start_dev = _read("scripts/start-dev.sh")
    lib_content = _read("scripts/lib/local_stack.sh")

    assert "--reload" not in start_all
    assert "start_local_backend" in start_all
    assert "nohup setsid" in lib_content
    assert "</dev/null" in lib_content
    assert "--reload" in start_dev


def test_start_all_always_restarts_owned_backend_via_full_stack_stop():
    start_all = _read("scripts/start-all.sh")
    lib_content = _read("scripts/lib/local_stack.sh")

    assert "--restart-backend" in start_all
    assert 'bash "$PROJECT_ROOT/scripts/stop-all.sh"' in start_all
    assert "owned_backend_pid" in lib_content
    assert "stop_owned_backend" in lib_content
    assert "restart_owned_backend" in lib_content
    assert "kill -TERM" in lib_content
    assert "kill -KILL" not in lib_content


def test_nginx_liveness_check_does_not_require_sudo_credentials():
    start_all = _read("scripts/start-all.sh")
    stop_all = _read("scripts/stop-all.sh")
    lib_content = _read("scripts/lib/common.sh")

    assert "process_exists" in lib_content
    assert "process_exists \"$NGINX_PID\"" in start_all
    assert "process_exists \"$NGINX_PID\"" in stop_all
    assert "sudo kill -0" not in start_all


def test_runtime_staleness_is_reported_and_stable_start_rebuilds_frontend():
    lib_content = _read("scripts/lib/local_stack.sh")
    status_content = _read("scripts/status.sh")
    start_content = _read("scripts/start-all.sh")

    assert "backend_runtime_stale" in lib_content
    assert "frontend_dist_stale" in lib_content
    assert "运行代码已过期" in status_content
    assert "按当前代码重新安装锁定依赖并构建前端" in start_content
    assert "npm ci --silent" in start_content
    assert "exit 1" in status_content


def test_files_newer_than_epoch_uses_real_mtimes(tmp_path):
    older = tmp_path / "older.py"
    newer = tmp_path / "newer.py"
    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")
    os.utime(older, (100, 100))
    os.utime(newer, (300, 300))
    command = (
        f'source "{PROJECT_ROOT / "scripts/lib/local_stack.sh"}"; '
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


def test_script_interfaces_are_stable_executable_facades():
    facades = (
        "start-all.sh",
        "start-dev.sh",
        "stop-all.sh",
        "status.sh",
        "doctor.sh",
        "db.sh",
        "docker.sh",
        "verify.sh",
        "run-cad-worker.sh",
    )
    for name in facades:
        path = PROJECT_ROOT / "scripts" / name
        assert path.is_file(), name
        assert path.stat().st_mode & stat.S_IXUSR, name
        assert "/lib/" in path.read_text(encoding="utf-8"), name


def test_script_implementations_are_classified_and_legacy_paths_retired():
    expected = (
        "scripts/lib/common.sh",
        "scripts/lib/local_stack.sh",
        "scripts/lib/database.sh",
        "scripts/lib/compose.sh",
        "scripts/lib/cad_worker.sh",
        "scripts/cad/benchmark_conversion.py",
        "scripts/windows/forward_to_win11.sh",
        "scripts/storage/reap.py",
        "scripts/storage/verify_transactions.py",
        "scripts/docs/check.py",
        "scripts/docs/generate_api.py",
    )
    for relative in expected:
        assert (PROJECT_ROOT / relative).is_file(), relative

    retired = (
        "scripts/benchmark_cad_conversion.py",
        "scripts/forward-to-win11.sh",
        "scripts/reap_storage.py",
        "scripts/verify_storage_transactions.py",
        "scripts/check_docs.py",
        "scripts/generate_api_docs.py",
    )
    for relative in retired:
        assert not (PROJECT_ROOT / relative).exists(), relative


def test_all_shell_interfaces_and_libraries_have_valid_syntax():
    scripts = sorted(
        path
        for path in (PROJECT_ROOT / "scripts").rglob("*.sh")
        if not path.name.startswith("tempCodeRunnerFile")
    )
    assert scripts
    for script in scripts:
        result = subprocess.run(
            ["bash", "-n", str(script)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, f"{script}: {result.stderr}"


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
    assert "DXF→Excel Stage" in content
    assert "cd Stages/dxf2excel && uv run pytest -q" in content
    assert "Steel DXF Classifier Stage" in content
    assert "cd Stages/steel_dxf_classifier_v1.1.0 && uv run pytest -q" in content


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
        "windows/forward_to_win11.sh",
        "run-cad-worker.sh",
        "storage/reap.py",
    ):
        assert command in content
    assert "--restart-backend" in content
    assert "--allow-blocked" in content


def test_nginx_proxies_fastapi_documentation_routes():
    for relative_path in (
        "infra/gateway/nginx/nginx.local.conf",
        "infra/gateway/nginx/nginx.conf",
    ):
        content = _read(relative_path)
        assert "location = /openapi.json" in content
        assert "location ~ ^/(docs|redoc)(/.*)?$" in content
        assert content.count("proxy_pass http://backend;") >= 5


def test_makefile_exposes_database_script_targets():
    content = _read("Makefile")

    assert "verify-quick:" in content
    assert "verify-full:" in content
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
