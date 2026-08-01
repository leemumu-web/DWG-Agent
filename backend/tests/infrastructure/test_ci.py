from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import yaml

from tests.support.paths import REPO_ROOT

CI_ENV_WRITER = REPO_ROOT / "scripts/ci/write_env.py"
CI_COMPOSE = REPO_ROOT / "compose.ci.yaml"
PRODUCTION_COMPOSE = REPO_ROOT / "compose.yaml"


def _run_env_writer(output: Path, *, project: str = "dwg-agent-ci-123-1"):
    return subprocess.run(
        [
            sys.executable,
            str(CI_ENV_WRITER),
            "--output",
            str(output),
            "--project",
            project,
            "--port",
            "21801",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "CI": "true"},
    )


def test_ci_compose_uses_unique_non_production_volumes():
    payload = yaml.safe_load(CI_COMPOSE.read_text(encoding="utf-8"))

    assert payload["name"] == "${CI_COMPOSE_PROJECT:?CI_COMPOSE_PROJECT is required}"
    names = {item["name"] for item in payload["volumes"].values()}
    assert names == {
        "${CI_COMPOSE_PROJECT}_app_var",
        "${CI_COMPOSE_PROJECT}_mysql_data",
        "${CI_COMPOSE_PROJECT}_minio_data",
    }
    assert not names & {
        "dwg-agent_app_var",
        "dwg-agent_mysql_data",
        "dwg-agent_minio_data",
    }


def test_production_gateway_keeps_lan_default_but_allows_ci_loopback_binding():
    payload = yaml.safe_load(PRODUCTION_COMPOSE.read_text(encoding="utf-8"))

    assert payload["services"]["nginx"]["ports"] == [
        "${HTTP_BIND_ADDRESS:-0.0.0.0}:${HTTP_PORT:-80}:8080"
    ]


def test_ci_env_writer_creates_private_placeholder_free_environment(tmp_path: Path):
    output = tmp_path / ".env.docker"

    result = _run_env_writer(output)

    assert result.returncode == 0, result.stderr
    content = output.read_text(encoding="utf-8")
    active_lines = [
        line for line in content.splitlines() if not line.lstrip().startswith("#")
    ]
    assert "CHANGE_ME_" not in "\n".join(active_lines)
    assert "HTTP_BIND_ADDRESS=127.0.0.1" in content
    assert "HTTP_PORT=21801" in content
    assert "DOCKER_MIN_FREE_GIB=5" in content
    assert "VERIFY_ADMIN_USERNAME=super_admin" in content
    assert "DWG_AGENT_IMAGE=dwg-agent-backend:ci-dwg-agent-ci-123-1" in content
    assert (
        "DWG_AGENT_FRONTEND_IMAGE=dwg-agent-frontend:ci-dwg-agent-ci-123-1"
        in content
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert "PASSWORD" not in result.stdout


def test_ci_env_writer_refuses_to_overwrite_existing_environment(tmp_path: Path):
    output = tmp_path / ".env.docker"
    output.write_text("preserve=true\n", encoding="utf-8")

    result = _run_env_writer(output)

    assert result.returncode != 0
    assert output.read_text(encoding="utf-8") == "preserve=true\n"


def test_ci_env_writer_requires_ci_context(tmp_path: Path):
    output = tmp_path / ".env.docker"
    env = os.environ.copy()
    env.pop("CI", None)
    env.pop("GITHUB_ACTIONS", None)

    result = subprocess.run(
        [
            sys.executable,
            str(CI_ENV_WRITER),
            "--output",
            str(output),
            "--project",
            "dwg-agent-ci-123-1",
            "--port",
            "21801",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert not output.exists()


def test_ci_env_writer_rejects_unsafe_project_name(tmp_path: Path):
    output = tmp_path / ".env.docker"

    result = _run_env_writer(output, project="dwg-agent")

    assert result.returncode != 0
    assert not output.exists()
