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
    for command in ("start", "setup-user", "init", "check", "status", "shell", "logs"):
        assert f'"{command}")' in content
    assert "DATABASE_URL" in content
    assert "mysql+pymysql" in content
    assert "SQLite" in content


def test_start_scripts_delegate_database_startup_to_db_script():
    for path in ("scripts/start-all.sh", "scripts/start-dev.sh"):
        content = _read(path)
        assert 'scripts/db.sh" start' in content
        assert 'scripts/db.sh" init' in content
        assert "ensure_service 3306" not in content


def test_init_db_script_delegates_to_database_entrypoint():
    content = _read("scripts/init_db.sh")

    assert 'scripts/db.sh" init' in content
    assert "python -m app.db.init_db" not in content


def test_stop_all_does_not_kill_unowned_backend_port():
    content = _read("scripts/stop-all.sh")

    assert "fuser -k" not in content
    assert "端口 8000 仍被占用" in content


def test_makefile_exposes_database_script_targets():
    content = _read("Makefile")

    for target in ("db-start:", "db-setup:", "db-init:", "db-status:", "db-shell:"):
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
