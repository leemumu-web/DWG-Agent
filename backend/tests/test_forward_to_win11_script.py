from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "forward-to-win11.sh"


@pytest.fixture
def fake_commands(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    ssh = bin_dir / "ssh"
    ssh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

socket=''
operation=''
previous=''
for argument in "$@"; do
    if [[ "$previous" == '-S' ]]; then
        socket="$argument"
    elif [[ "$previous" == '-O' ]]; then
        operation="$argument"
    fi
    previous="$argument"
done

printf '%s\\n' "$*" >> "${FAKE_SSH_LOG:?}"
[[ -n "$socket" ]] || exit 64
marker="${socket}.fake-active"

case "$operation" in
    check)
        [[ -f "$marker" ]]
        ;;
    exit)
        rm -f -- "$marker"
        ;;
    '')
        [[ "${FAKE_SSH_START_FAIL:-0}" != '1' ]] || exit 73
        mkdir -p -- "$(dirname -- "$socket")"
        : > "$marker"
        ;;
    *)
        exit 65
        ;;
esac
""",
        encoding="utf-8",
    )
    ssh.chmod(0o755)

    ss = bin_dir / "ss"
    ss.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${FAKE_SS_LISTENING:-1}" == '1' ]]; then
    address="${FAKE_SS_ADDRESS:-127.0.0.1}"
    port="${FAKE_SS_PORT:-8080}"
    printf 'LISTEN 0 128 %s:%s 0.0.0.0:*\\n' "$address" "$port"
fi
""",
        encoding="utf-8",
    )
    ss.chmod(0o755)
    return bin_dir


def run_script(
    tmp_path: Path,
    fake_commands: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command_env = os.environ.copy()
    command_env.update(
        {
            "PATH": f"{fake_commands}:{command_env['PATH']}",
            "XDG_RUNTIME_DIR": str(tmp_path / "runtime"),
            # Keep the synthetic Unix-socket path below the platform limit even
            # when pytest gives this test a deliberately descriptive directory.
            "FORWARD_RUNTIME_DIR": str(tmp_path / "r"),
            "FAKE_SSH_LOG": str(tmp_path / "ssh.log"),
        }
    )
    if env:
        command_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=PROJECT_ROOT,
        env=command_env,
        text=True,
        capture_output=True,
        check=False,
    )


def ssh_calls(tmp_path: Path) -> list[str]:
    log_path = tmp_path / "ssh.log"
    if not log_path.exists():
        return []
    return log_path.read_text(encoding="utf-8").splitlines()


def startup_calls(tmp_path: Path) -> list[str]:
    return [line for line in ssh_calls(tmp_path) if " -M " in f" {line} "]


def control_socket_from_call(call: str) -> Path:
    arguments = call.split()
    return Path(arguments[arguments.index("-S") + 1])


def test_help_documents_commands_defaults_and_overrides(
    tmp_path: Path, fake_commands: Path
) -> None:
    result = run_script(tmp_path, fake_commands, "--help")

    assert result.returncode == 0
    assert "start|stop|restart|status" in result.stdout
    assert "win11" in result.stdout
    assert "8080" in result.stdout
    assert "--remote-port" in result.stdout
    assert "FORWARD_REMOTE_HOST" in result.stdout


@pytest.mark.parametrize(
    "value", ["0", "65536", "abc", "80.5", "18446744073709551617"]
)
def test_rejects_invalid_port_before_running_ssh(
    tmp_path: Path, fake_commands: Path, value: str
) -> None:
    result = run_script(
        tmp_path, fake_commands, "status", "--remote-port", value
    )

    assert result.returncode == 2
    assert "remote port" in result.stderr.lower()
    assert ssh_calls(tmp_path) == []


def test_rejects_unknown_option(tmp_path: Path, fake_commands: Path) -> None:
    result = run_script(tmp_path, fake_commands, "start", "--surprise")

    assert result.returncode == 2
    assert "unknown option" in result.stderr.lower()
    assert ssh_calls(tmp_path) == []


def test_start_uses_secure_control_master_and_is_idempotent(
    tmp_path: Path, fake_commands: Path
) -> None:
    first = run_script(tmp_path, fake_commands, "start")
    second = run_script(tmp_path, fake_commands, "start")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "already active" in second.stdout.lower()
    starts = startup_calls(tmp_path)
    assert len(starts) == 1
    call = starts[0]
    assert "-M" in call
    assert "-S" in call
    assert "-fNT" in call
    assert "ControlPersist=yes" in call
    assert "ExitOnForwardFailure=yes" in call
    assert "ServerAliveInterval=30" in call
    assert "ServerAliveCountMax=5" in call
    assert "-R 127.0.0.1:8080:127.0.0.1:8080" in call
    assert call.endswith(" win11")


def test_status_stop_and_restart_manage_only_the_controlled_tunnel(
    tmp_path: Path, fake_commands: Path
) -> None:
    assert run_script(tmp_path, fake_commands, "start").returncode == 0

    active = run_script(tmp_path, fake_commands, "status")
    stopped = run_script(tmp_path, fake_commands, "stop")
    inactive = run_script(tmp_path, fake_commands, "status")
    restarted = run_script(tmp_path, fake_commands, "restart")

    assert active.returncode == 0
    assert "active" in active.stdout.lower()
    assert stopped.returncode == 0
    assert "stopped" in stopped.stdout.lower()
    assert inactive.returncode == 3
    assert "not running" in inactive.stdout.lower()
    assert restarted.returncode == 0, restarted.stderr
    assert len(startup_calls(tmp_path)) == 2
    assert any(" -O exit " in f" {call} " for call in ssh_calls(tmp_path))


def test_legacy_stop_alias_remains_supported(
    tmp_path: Path, fake_commands: Path
) -> None:
    assert run_script(tmp_path, fake_commands, "start").returncode == 0

    result = run_script(tmp_path, fake_commands, "--stop")

    assert result.returncode == 0
    assert run_script(tmp_path, fake_commands, "status").returncode == 3


def test_command_line_overrides_environment_and_changes_socket_identity(
    tmp_path: Path, fake_commands: Path
) -> None:
    result = run_script(
        tmp_path,
        fake_commands,
        "start",
        "--host",
        "edge-win",
        "--remote-address",
        "127.0.0.2",
        "--remote-port",
        "18080",
        "--local-address",
        "127.0.0.2",
        "--local-port",
        "18081",
        env={
            "FORWARD_REMOTE_HOST": "ignored-host",
            "FORWARD_REMOTE_PORT": "28080",
            "FAKE_SS_ADDRESS": "127.0.0.2",
            "FAKE_SS_PORT": "18081",
        },
    )

    assert result.returncode == 0, result.stderr
    call = startup_calls(tmp_path)[0]
    assert "-R 127.0.0.2:18080:127.0.0.2:18081" in call
    assert call.endswith(" edge-win")
    assert "http://127.0.0.2:18080" in result.stdout


def test_start_rejects_missing_local_listener_without_calling_ssh(
    tmp_path: Path, fake_commands: Path
) -> None:
    result = run_script(
        tmp_path,
        fake_commands,
        "start",
        env={"FAKE_SS_LISTENING": "0"},
    )

    assert result.returncode != 0
    assert "not listening" in result.stderr.lower()
    assert "scripts/start-all.sh" in result.stderr
    assert ssh_calls(tmp_path) == []


def test_start_failure_is_propagated_and_not_reported_active(
    tmp_path: Path, fake_commands: Path
) -> None:
    result = run_script(
        tmp_path,
        fake_commands,
        "start",
        env={"FAKE_SSH_START_FAIL": "1"},
    )

    assert result.returncode == 73
    assert "active" not in result.stdout.lower()


def test_status_removes_stale_control_socket(
    tmp_path: Path, fake_commands: Path
) -> None:
    initial = run_script(tmp_path, fake_commands, "status")
    assert initial.returncode == 3
    check_call = ssh_calls(tmp_path)[-1]
    socket_path = control_socket_from_call(check_call)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.write_text("stale", encoding="utf-8")

    result = run_script(tmp_path, fake_commands, "status")

    assert result.returncode == 3
    assert not socket_path.exists()


def test_status_does_not_require_local_service(
    tmp_path: Path, fake_commands: Path
) -> None:
    result = run_script(
        tmp_path,
        fake_commands,
        "status",
        env={"FAKE_SS_LISTENING": "0"},
    )

    assert result.returncode == 3
    assert "not running" in result.stdout.lower()
