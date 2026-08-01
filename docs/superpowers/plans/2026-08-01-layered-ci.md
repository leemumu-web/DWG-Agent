# Layered Continuous Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add mandatory, isolated GitHub Actions CI for every pull request and every push to `main`, including full code, Stage, frontend browser, and protected-container validation without publishing artifacts or touching production data.

**Architecture:** Keep GitHub workflow YAML declarative and move reproducible behavior into `scripts/ci/`. The main workflow fans out code checks, then calls a reusable container workflow; a CI-only Compose override gives every run unique volumes while the production Compose defaults remain unchanged. Infrastructure tests parse the workflow and exercise environment generation before any workflow implementation is accepted.

**Tech Stack:** GitHub Actions, Bash, Python 3.12, uv, pytest, Ruff, Node 22, npm, Playwright Chromium, Docker Buildx, Docker Compose, MySQL, MinIO.

**Execution note:** The repository owner prohibits subagents, so the root agent executes this plan inline and performs self-review before push.

---

## Implementation tasks

### Task 1: Lock the CI data-isolation contract

**Files:**
- Create: `backend/tests/infrastructure/test_ci.py`
- Create: `compose.ci.yaml`
- Create: `scripts/ci/write_env.py`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Modify: `.env.docker.example`

- [ ] **Step 1: Write failing tests for the CI environment and volume boundary**

Add tests that:

```python
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


def test_ci_env_writer_creates_private_placeholder_free_environment(tmp_path: Path):
    output = tmp_path / ".env.docker"
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
        env={**os.environ, "CI": "true"},
    )
    assert result.returncode == 0, result.stderr
    content = output.read_text(encoding="utf-8")
    assert "CHANGE_ME_" not in "\n".join(
        line for line in content.splitlines() if not line.lstrip().startswith("#")
    )
    assert "HTTP_BIND_ADDRESS=127.0.0.1" in content
    assert "HTTP_PORT=21801" in content
    assert "DOCKER_MIN_FREE_GIB=5" in content
    assert "VERIFY_ADMIN_USERNAME=super_admin" in content
    assert "dwg-agent-backend:ci-dwg-agent-ci-123-1" in content
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert "PASSWORD" not in result.stdout


def test_ci_env_writer_refuses_to_overwrite_existing_environment(tmp_path: Path):
    output = tmp_path / ".env.docker"
    output.write_text("preserve=true\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(CI_ENV_WRITER), "--output", str(output), "--project", "dwg-agent-ci-1", "--port", "21801"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "CI": "true"},
    )
    assert result.returncode != 0
    assert output.read_text(encoding="utf-8") == "preserve=true\n"
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
uv run pytest -q tests/infrastructure/test_ci.py
```

Expected: FAIL because `compose.ci.yaml` and `scripts/ci/write_env.py` do not exist.

- [ ] **Step 3: Implement the CI override and environment writer**

Create `compose.ci.yaml`:

```yaml
name: ${CI_COMPOSE_PROJECT:?CI_COMPOSE_PROJECT is required}

volumes:
  app_var:
    name: ${CI_COMPOSE_PROJECT}_app_var
  mysql_data:
    name: ${CI_COMPOSE_PROJECT}_mysql_data
  minio_data:
    name: ${CI_COMPOSE_PROJECT}_minio_data
```

Change the Nginx production port mapping to preserve the current LAN default while allowing CI loopback binding:

```yaml
ports:
  - "${HTTP_BIND_ADDRESS:-0.0.0.0}:${HTTP_PORT:-80}:8080"
```

Add `HTTP_BIND_ADDRESS=0.0.0.0` to both environment examples. Implement `write_env.py` with `argparse`, `secrets`, and atomic mode-0600 creation. It must:

- require `CI=true` or `GITHUB_ACTIONS=true`;
- validate project as `dwg-agent-ci-[a-zA-Z0-9-]+` and port 1024–65535;
- refuse an existing output;
- replace active `CHANGE_ME_*` values with generated MySQL, MinIO, JWT and administrator credentials;
- set `VERIFY_ADMIN_USERNAME=super_admin` and the generated administrator password;
- set loopback binding, unique image tags, worker concurrency 1, MySQL buffer pool 512M and `DOCKER_MIN_FREE_GIB=5`;
- write with `os.open(..., O_CREAT | O_EXCL, 0o600)` and never print values.

- [ ] **Step 4: Run focused and existing Compose tests**

```bash
cd backend
uv run pytest -q tests/infrastructure/test_ci.py tests/infrastructure/test_compose.py tests/infrastructure/test_config.py
```

Expected: PASS.

- [ ] **Step 5: Commit the isolation layer**

```bash
git add backend/tests/infrastructure/test_ci.py compose.ci.yaml scripts/ci/write_env.py compose.yaml .env.example .env.docker.example
git commit -m "ci: isolate runtime data and credentials"
```

### Task 2: Make protected runtime checks reusable by CI

**Files:**
- Modify: `backend/tests/infrastructure/test_server_release.py`
- Modify: `scripts/release.sh`
- Create: `scripts/ci/run_container_validation.sh`

- [ ] **Step 1: Write failing tests for sourceable release checks and cleanup guarantees**

Add assertions that sourcing `scripts/release.sh` defines `release_verify_protected_image` and `release_verify_oda_roundtrip` without executing the CLI case statement. Add static contract assertions for `run_container_validation.sh`:

```python
def test_release_runtime_checks_are_sourceable_for_ci():
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{RELEASE_SCRIPT}"; declare -F release_verify_protected_image; declare -F release_verify_oda_roundtrip',
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_ci_container_runner_is_fail_closed_and_self_cleaning():
    source = (REPO_ROOT / "scripts/ci/run_container_validation.sh").read_text(encoding="utf-8")
    assert "set -Eeuo pipefail" in source
    assert "compose.ci.yaml" in source
    assert "release_verify_protected_image" in source
    assert "release_verify_oda_roundtrip" in source
    assert "verify_image_archive.py" in source
    assert "compose_wait_for_healthy_services" in source
    assert "compose_verify_storage" in source
    assert "verify_live_remnant.py" in source
    assert "down --volumes --remove-orphans" in source
    assert "trap" in source
```

- [ ] **Step 2: Run and verify RED**

```bash
cd backend
uv run pytest -q tests/infrastructure/test_server_release.py -k 'sourceable_for_ci or ci_container_runner'
```

Expected: FAIL because sourcing invokes usage and the CI runner is absent.

- [ ] **Step 3: Guard the release CLI and implement the container runner**

Wrap the bottom-level release command dispatch:

```bash
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    case "${1:-}" in
        bundle) shift; release_bundle "$@" ;;
        -h|--help|"") release_usage ;;
        *) release_usage; release_die "unknown command: $1" ;;
    esac
fi
```

Implement `run_container_validation.sh` so it:

1. requires `CI_COMPOSE_PROJECT`, `CI_HTTP_PORT`, absent `.env.docker`, and four prebuilt CI image names or explicitly builds them;
2. calls `write_env.py`;
3. constructs one Compose command from `compose.yaml` plus `compose.ci.yaml`;
4. verifies the protected backend image and ODA round trip by sourcing `release.sh`;
5. saves only the backend image to a `mktemp -d` archive and runs `verify_image_archive.py`;
6. starts all profiles with `--no-build`, waits for the exact complete service set, then runs runtime matrix, storage and remnant probes;
7. on failure prints `compose ps`, bounded logs, `df`, and named volume references without printing `.env.docker`;
8. always runs exact-project `down --volumes --remove-orphans`, removes its temp directory and `.env.docker`, and preserves the original exit status.

- [ ] **Step 4: Verify scripts and release contracts**

```bash
bash -n scripts/ci/run_container_validation.sh scripts/release.sh
cd backend
uv run pytest -q tests/infrastructure/test_server_release.py tests/infrastructure/test_scripts.py tests/infrastructure/test_ci.py
```

Expected: PASS.

- [ ] **Step 5: Commit runtime validation reuse**

```bash
git add scripts/release.sh scripts/ci/run_container_validation.sh backend/tests/infrastructure/test_server_release.py
git commit -m "ci: reuse protected runtime release gates"
```

### Task 3: Prove the browser suite's runtime boundary

**Evidence:** A real Vite-only run reached the built SPA, but the suite immediately
required `/api/v1/auth/sessions`, runtime configuration, MySQL and MinIO. Only two
presentation tests were independent; treating the remaining failures as frontend
regressions would be incorrect. The preview process group was successfully reaped.

- [x] **Step 1:** Reject the Vite-only browser runner and delete the experimental script.
- [x] **Step 2:** Put Playwright after protected-stack health, runtime, storage and remnant probes in `run_container_validation.sh`.
- [x] **Step 3:** Keep the standalone frontend job responsible for deterministic architecture, TypeScript and Vite production build checks.
- [ ] **Step 4:** Run all 144 browser tests against the isolated production-shaped Nginx endpoint during the container gate.

### Task 4: Define and lock the GitHub Actions workflows

**Files:**
- Modify: `backend/tests/infrastructure/test_ci.py`
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/container-ci.yml`
- Modify: `scripts/verify.sh`

- [ ] **Step 1: Write failing workflow structure tests**

Use a YAML loader that preserves the key `on` as a string. Tests must assert:

- `ci.yml` has unfiltered `pull_request`, `push.branches == ["main"]`, and `workflow_dispatch`;
- top-level permissions equal `{"contents": "read"}` and concurrency cancels old runs;
- required jobs are `quality`, `backend`, `stages`, `frontend`, `container`, and `required`;
- full backend pytest, Stage matrix, production frontend build, and reusable container call are present; Playwright is enforced inside that protected full-stack call;
- no required job uses `continue-on-error`;
- `container-ci.yml` supports `workflow_call`, schedule, and dispatch;
- all remote `uses:` values end in a 40-character hexadecimal SHA, while the only local use is `./.github/workflows/container-ci.yml`;
- neither workflow contains `pull_request_target`, `packages: write`, `id-token: write`, `docker push`, GPG, SSH or production volume names;
- every job has a timeout and the frontend failure artifact retention is 7 days;
- `scripts/verify.sh quick` includes `tests/infrastructure/test_ci.py`.

- [ ] **Step 2: Run and verify RED**

```bash
cd backend
uv run pytest -q tests/infrastructure/test_ci.py -k workflow
```

Expected: FAIL because `.github/workflows` is absent.

- [ ] **Step 3: Implement `ci.yml`**

Use `ubuntu-24.04`, Python 3.12, Node 22, lockfile caches and these immutable actions:

```yaml
actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5
astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38 # v6
actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
```

The Stage matrix uses frozen independent projects for the six committed locks; BH reader tests and the Steel Split CLI contract run in the backend-integrated environment. Set `fail-fast: false`. The frontend job performs the production build. The local reusable container job depends on all four code jobs, installs Chromium, runs Playwright against the healthy Nginx endpoint, and uploads only Playwright failure output. The `required` job runs with `if: always()` and fails unless every dependency result is `success`.

- [ ] **Step 4: Implement reusable `container-ci.yml`**

Use these immutable Docker actions:

```yaml
docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f # v3
docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8 # v6
```

Build backend target `protected` and frontend with `load: true`, `push: false`, and separate GHA cache scopes. Derive `CI_COMPOSE_PROJECT` and a loopback port from `github.run_id` plus `github.run_attempt`, then call `run_container_validation.sh`. Do not upload image archives or `.env.docker`.

- [ ] **Step 5: Make quick verification enforce CI contracts**

Add `tests/infrastructure/test_ci.py` to the focused pytest list in `scripts/verify.sh`.

- [ ] **Step 6: Run CI contract and quick gates**

```bash
cd backend
uv run pytest -q tests/infrastructure/test_ci.py
cd ..
bash scripts/verify.sh quick
```

Expected: PASS with no ignored failure.

- [ ] **Step 7: Commit workflows**

```bash
git add .github/workflows/ci.yml .github/workflows/container-ci.yml backend/tests/infrastructure/test_ci.py scripts/verify.sh
git commit -m "ci: enforce full pull request and main gates"
```

### Task 5: Run the exact CI gates locally

**Files:**
- Modify only if a gate reveals a real CI defect in files owned by Tasks 1–4.

- [ ] **Step 1: Run complete backend and Stage tests**

```bash
cd backend && uv run pytest -q && cd ..
for stage in dwg2dxf dxf2dwg dxf2excel steel_dxf_classifier_v1.1.0 excel_final remnant_drawing_reader; do
  (cd "Stages/$stage" && uv sync --frozen && uv run pytest -q)
done
(cd backend && uv run pytest -q ../Stages/bh_left_right_reader/tests ../Stages/steel_dxf_split_v1.5.2/tests)
```

Expected: PASS or only existing explicitly skipped tests. Any command/path defect must be fixed in the workflow and contract before continuing.

- [ ] **Step 2: Run frontend build**

```bash
cd frontend && npm ci && npm run build && cd ..
```

Expected: PASS. Playwright runs in Step 3 after the isolated full stack is healthy.

- [ ] **Step 3: Run container CI in a clean temporary worktree**

Create a temporary worktree at `HEAD` so the existing production `.env.docker` and running port 18080 are untouched. Use a unique project and port, prebuild or load CI images, execute `run_container_validation.sh`, and then remove the worktree. Before and after, compare references and database counts for `dwg-agent_mysql_data`, `dwg-agent_minio_data`, and `dwg-agent_app_var`.

Expected: all 16 source services healthy, runtime matrix/storage/remnant probes pass, CI volumes are removed, and formal volume references/data are unchanged.

- [ ] **Step 4: Run the full repository gate**

```bash
bash scripts/verify.sh full
```

Expected: `FAIL=0`; external-only gates must not be silently converted to success.

- [ ] **Step 5: Review the complete diff and commit any gate-driven fixes**

```bash
git diff --check
git status --short
git diff origin/main...HEAD -- .github compose.ci.yaml scripts/ci scripts/verify.sh compose.yaml .env.example .env.docker.example backend/tests/infrastructure/test_ci.py scripts/release.sh backend/tests/infrastructure/test_server_release.py
```

Do not add `Stages/excel_final/data/`, `output/`, `releases/`, `.env.docker`, browser reports or temporary artifacts.

### Task 6: Push and verify the remote CI run

**Files:**
- No source files unless remote GitHub validation exposes a real defect.

- [ ] **Step 1: Verify branch and remote safety**

```bash
git status --short --branch
git remote -v
git fetch origin
git merge-base --is-ancestor origin/main HEAD
```

Expected: local `main` contains `origin/main`; only intentional untracked runtime/sample directories remain.

- [ ] **Step 2: Push `main`**

```bash
git push origin main
```

Expected: push succeeds without force.

- [ ] **Step 3: Confirm GitHub registered and started both workflows**

Use GitHub CLI or the official Actions API to confirm `.github/workflows/ci.yml` and `.github/workflows/container-ci.yml` are active and the pushed SHA has a CI run. Record the run URL and current conclusion. Do not claim success while a run is queued/in progress or blocked by account quota.

- [ ] **Step 4: Final status**

Report pushed commit SHA, remote branch, local test evidence, Actions run URL/status, and any platform-side blocker. If GitHub CI fails because of workflow defects, fix locally, re-run the relevant gate, commit, push, and re-check until the repository-side CI is green.
