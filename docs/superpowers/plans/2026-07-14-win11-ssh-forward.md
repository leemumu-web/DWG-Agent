# Win11 SSH Forwarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fragile PID/`pgrep` tunnel wrapper with a configurable, idempotent SSH ControlMaster lifecycle and publish it with deterministic tests.

**Architecture:** `scripts/forward-to-win11.sh` remains one focused Bash entry point. It parses and validates configuration, derives a per-configuration control socket, serializes lifecycle changes with `flock`, checks the local target listener, and delegates connection identity to `ssh -O check/exit`. Pytest executes the real script against fake `ssh` and `ss` binaries so no Win11 connection is required.

**Tech Stack:** Bash 5, OpenSSH ControlMaster, util-linux `flock`, iproute2 `ss`, pytest, Ruff/ShellCheck, Git.

---

## File structure

- Create `backend/tests/test_forward_to_win11_script.py`: black-box CLI and lifecycle tests using an isolated fake command directory.
- Modify `scripts/forward-to-win11.sh`: argument parsing, validation, control-socket lifecycle, locking and diagnostics.
- Modify `docs/configuration.md`: operator-facing command, defaults, overrides and remote bind safety.

### Task 1: Lock down the CLI contract

**Files:**
- Create: `backend/tests/test_forward_to_win11_script.py`
- Modify: `scripts/forward-to-win11.sh`

- [ ] **Step 1: Write failing help, override and validation tests**

Create a subprocess helper that supplies an isolated runtime directory and fake command `PATH`, then assert the public contract:

```python
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "forward-to-win11.sh"


def run_script(tmp_path: Path, *args: str, env: dict[str, str] | None = None):
    command_env = os.environ.copy()
    command_env.update({"XDG_RUNTIME_DIR": str(tmp_path / "runtime")})
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


def test_help_documents_commands_and_defaults(tmp_path: Path):
    result = run_script(tmp_path, "--help")
    assert result.returncode == 0
    assert "start|stop|restart|status" in result.stdout
    assert "win11" in result.stdout
    assert "8080" in result.stdout


@pytest.mark.parametrize("value", ["0", "65536", "abc", "80.5"])
def test_rejects_invalid_port_before_running_ssh(tmp_path: Path, value: str):
    result = run_script(tmp_path, "status", "--remote-port", value)
    assert result.returncode == 2
    assert "remote port" in result.stderr.lower()
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `cd backend && uv run pytest tests/test_forward_to_win11_script.py -q`

Expected: FAIL because the current script has no help, subcommand or option parser.

- [ ] **Step 3: Implement minimal parsing and validation**

In `scripts/forward-to-win11.sh`, define defaults from environment and parse subcommands/options without executing lifecycle commands during parsing:

```bash
COMMAND="start"
REMOTE_HOST="${FORWARD_REMOTE_HOST:-win11}"
REMOTE_BIND_ADDRESS="${FORWARD_REMOTE_BIND_ADDRESS:-127.0.0.1}"
REMOTE_PORT="${FORWARD_REMOTE_PORT:-8080}"
LOCAL_ADDRESS="${FORWARD_LOCAL_ADDRESS:-127.0.0.1}"
LOCAL_PORT="${FORWARD_LOCAL_PORT:-8080}"

validate_port() {
    local label="$1" value="$2"
    [[ "$value" =~ ^[0-9]+$ ]] && (( value >= 1 && value <= 65535 )) || {
        printf 'Error: %s must be an integer from 1 to 65535\n' "$label" >&2
        exit 2
    }
}
```

Support `--host`, `--remote-address`, `--remote-port`, `--local-address`, `--local-port`, `--runtime-dir`, `--help`, plus compatibility alias `--stop`. Reject unknown options and extra positional arguments with exit 2.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `cd backend && uv run pytest tests/test_forward_to_win11_script.py -q`

Expected: all Task 1 tests PASS.

- [ ] **Step 5: Commit the CLI contract**

```bash
git add scripts/forward-to-win11.sh backend/tests/test_forward_to_win11_script.py
git commit -m "feat: define Win11 tunnel command contract"
```

### Task 2: Replace PID discovery with a ControlMaster lifecycle

**Files:**
- Modify: `backend/tests/test_forward_to_win11_script.py`
- Modify: `scripts/forward-to-win11.sh`

- [ ] **Step 1: Add failing lifecycle tests with fake SSH**

Add a fixture that writes an executable `ssh` shim. The shim stores a marker beside the requested `-S` socket, returns success for `-O check` only while the marker exists, removes it for `-O exit`, and can fail startup when `FAKE_SSH_START_FAIL=1`:

```python
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
for arg in "$@"; do
  [[ "$previous" == '-S' ]] && socket="$arg"
  [[ "$previous" == '-O' ]] && operation="$arg"
  previous="$arg"
done
marker="${socket}.fake-active"
case "$operation" in
  check) [[ -f "$marker" ]] ;;
  exit) rm -f "$marker" ;;
  '') [[ "${FAKE_SSH_START_FAIL:-0}" != 1 ]] || exit 73; mkdir -p "$(dirname "$socket")"; : > "$marker" ;;
esac
printf '%s\\n' "$*" >> "${FAKE_SSH_LOG:?}"
""",
        encoding="utf-8",
    )
    ssh.chmod(0o755)
    ss = bin_dir / "ss"
    ss.write_text("#!/usr/bin/env bash\nprintf '%s\\n' 'LISTEN 0 128 127.0.0.1:8080 0.0.0.0:*'\n", encoding="utf-8")
    ss.chmod(0o755)
    return bin_dir
```

Test `start` then `status` returns active, repeated `start` is idempotent and does not add another startup entry, `stop` makes `status` return code 3, and `restart` performs exit then a new start. Assert the startup log contains `-M`, `-S`, `-fNT`, `ExitOnForwardFailure=yes`, keepalives and `127.0.0.1:8080:127.0.0.1:8080`.

- [ ] **Step 2: Run the lifecycle tests and confirm RED**

Run: `cd backend && uv run pytest tests/test_forward_to_win11_script.py -q`

Expected: lifecycle assertions FAIL because the old PID/`pgrep` implementation is still present.

- [ ] **Step 3: Implement socket identity, locking and lifecycle functions**

Derive a bounded socket path from `sha256sum` (or `cksum` fallback), create a user-private runtime directory, acquire an exclusive file lock for mutating commands, and implement:

```bash
is_running() {
    ssh -S "$CONTROL_SOCKET" -O check "$REMOTE_HOST" >/dev/null 2>&1
}

start_tunnel() {
    if is_running; then
        printf 'Tunnel already active\n'
        return 0
    fi
    rm -f -- "$CONTROL_SOCKET"
    ssh -M -S "$CONTROL_SOCKET" -fNT \
        -o ControlPersist=yes \
        -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=5 \
        -R "${REMOTE_BIND_ADDRESS}:${REMOTE_PORT}:${LOCAL_ADDRESS}:${LOCAL_PORT}" \
        "$REMOTE_HOST"
    is_running || { printf 'Error: SSH master did not become active\n' >&2; return 1; }
}

stop_tunnel() {
    if ! is_running; then
        rm -f -- "$CONTROL_SOCKET"
        printf 'No tunnel running\n'
        return 0
    fi
    ssh -S "$CONTROL_SOCKET" -O exit "$REMOTE_HOST" >/dev/null
    rm -f -- "$CONTROL_SOCKET"
}
```

Make `status` return 0 when active and 3 when stopped. Preserve the SSH startup failure status and remove stale sockets.

- [ ] **Step 4: Run the lifecycle tests and confirm GREEN**

Run: `cd backend && uv run pytest tests/test_forward_to_win11_script.py -q`

Expected: all lifecycle tests PASS.

- [ ] **Step 5: Commit the reliable lifecycle**

```bash
git add scripts/forward-to-win11.sh backend/tests/test_forward_to_win11_script.py
git commit -m "fix: manage Win11 tunnel by SSH control socket"
```

### Task 3: Add local readiness and failure-path coverage

**Files:**
- Modify: `backend/tests/test_forward_to_win11_script.py`
- Modify: `scripts/forward-to-win11.sh`

- [ ] **Step 1: Write failing readiness and stale-state tests**

Extend the fake `ss` command to emit no listener when `FAKE_SS_LISTENING=0`. Assert:

```python
def test_start_rejects_missing_local_listener(tmp_path: Path, fake_commands: Path):
    result = run_with_fakes(tmp_path, fake_commands, "start", env={"FAKE_SS_LISTENING": "0"})
    assert result.returncode != 0
    assert "not listening" in result.stderr.lower()
    assert read_ssh_log(tmp_path) == []


def test_start_failure_is_propagated_and_not_reported_active(tmp_path: Path, fake_commands: Path):
    result = run_with_fakes(tmp_path, fake_commands, "start", env={"FAKE_SSH_START_FAIL": "1"})
    assert result.returncode == 73
    assert "active" not in result.stdout.lower()
```

Also create a stale socket file before `status`, assert exit 3, then assert the stale file is removed.

- [ ] **Step 2: Run the new tests and confirm RED**

Run: `cd backend && uv run pytest tests/test_forward_to_win11_script.py -q`

Expected: readiness or stale-cleanup tests FAIL.

- [ ] **Step 3: Implement dependency and local listener checks**

Before start, require `ssh`, `flock`, `ss` and a digest utility. Match the requested local address/port against `ss -H -ltn` without accepting substring port matches. Treat wildcard listeners as satisfying a concrete local address. Emit:

```text
Error: local target 127.0.0.1:8080 is not listening. Start the project with: bash scripts/start-all.sh
```

Do not perform the local listener check for `status` or `stop` so operators can always clean up a tunnel after the local service stops.

- [ ] **Step 4: Run all script tests and confirm GREEN**

Run: `cd backend && uv run pytest tests/test_forward_to_win11_script.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit readiness hardening**

```bash
git add scripts/forward-to-win11.sh backend/tests/test_forward_to_win11_script.py
git commit -m "test: harden Win11 tunnel failure paths"
```

### Task 4: Document and verify the operator workflow

**Files:**
- Modify: `docs/configuration.md`
- Modify: `scripts/forward-to-win11.sh`
- Test: `backend/tests/test_forward_to_win11_script.py`

- [ ] **Step 1: Document exact commands and safety boundary**

Add a “Win11 本地访问转发” subsection documenting:

```bash
bash scripts/forward-to-win11.sh start
bash scripts/forward-to-win11.sh status
bash scripts/forward-to-win11.sh restart --remote-port 18080
bash scripts/forward-to-win11.sh stop
```

State that the default remote bind is `127.0.0.1`, configuration precedence is CLI over `FORWARD_*` environment variables over defaults, and `GatewayPorts` is intentionally not changed.

- [ ] **Step 2: Run shell and focused checks**

Run:

```bash
bash -n scripts/forward-to-win11.sh
command -v shellcheck >/dev/null && shellcheck scripts/forward-to-win11.sh
cd backend && uv run pytest tests/test_forward_to_win11_script.py tests/test_scripts.py -q
cd .. && make docs-check
git diff --check
```

Expected: Bash and ShellCheck report no errors; pytest and docs check PASS; diff check emits no output.

- [ ] **Step 3: Run repository-relevant regression**

Run:

```bash
cd backend && uv run ruff check app tests ../scripts
cd .. && docker compose config --quiet
```

Expected: both commands exit 0.

- [ ] **Step 4: Commit documentation and final refinements**

```bash
git add scripts/forward-to-win11.sh backend/tests/test_forward_to_win11_script.py docs/configuration.md
git commit -m "docs: document Win11 tunnel operations"
```

### Task 5: Integrate and publish main

**Files:**
- No additional source changes expected.

- [ ] **Step 1: Confirm exact publish scope**

Run: `git status -sb && git diff --check && git log --oneline origin/main..main`

Expected: current branch is `main`; working tree is clean; the output contains only the CAD conversion commits plus this Win11 tunnel work.

- [ ] **Step 2: Confirm GitHub authentication and remote relationship**

Run:

```bash
gh --version
gh auth status
git fetch origin main
git rev-list --left-right --count origin/main...main
```

Expected: `gh` is installed/authenticated; remote is not ahead of local. If remote is ahead, stop and reconcile without force pushing.

- [ ] **Step 3: Push main without force**

Run: `git push origin main`

Expected: fast-forward update of `origin/main` to the final local commit.

- [ ] **Step 4: Verify the published commit**

Run:

```bash
git fetch origin main
test "$(git rev-parse main)" = "$(git rev-parse origin/main)"
git status -sb
```

Expected: local and remote hashes match and `main...origin/main` has no ahead/behind marker.
