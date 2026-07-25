# DXF Classification Autoscale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow one to three different project-level DXF classification Jobs to run concurrently while retaining one idle classification process.

**Architecture:** Keep one dedicated `dxf_classification` Celery node and replace its fixed prefork size with Celery autoscale. The local scripts and Compose use the same minimum and maximum environment variables; runtime status verifies the launched command rather than trusting configuration text.

**Tech Stack:** Bash, Celery prefork autoscaler, Docker Compose, Pytest, MySQL-backed Job claims.

---

## File map

- Modify `scripts/lib/cad_worker.sh`: define and validate autoscale configuration, add autoscale metadata to the worker topology, and launch the classification worker with `--autoscale`.
- Modify `scripts/status.sh`: parse autoscale metadata and reject a classification worker started with stale fixed-concurrency arguments.
- Modify `compose.yaml`: apply the same autoscale settings to the Compose classification worker.
- Modify `.env.example` and `.env.docker.example`: publish the default `1–3` settings.
- Modify `docs/reference/configuration.md` and `docs/guides/deployment.md`: document behavior and tuning boundary.
- Modify `backend/tests/infrastructure/test_scripts.py`: cover local topology defaults, overrides, validation, and status verification.
- Modify `backend/tests/infrastructure/test_compose.py`: cover Compose command and worker capacity metadata.

### Task 1: Lock the local autoscale contract with failing tests

**Files:**
- Modify: `backend/tests/infrastructure/test_scripts.py`
- Test: `backend/tests/infrastructure/test_scripts.py`

- [ ] **Step 1: Add a helper that sources the real worker library**

```python
def _load_worker_specs(**overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            (
                f'source "{PROJECT_ROOT}/scripts/lib/cad_worker.sh"; '
                'printf "%s\\n" "${WORKER_SPECS[@]}"'
            ),
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, **overrides},
        text=True,
        capture_output=True,
        check=False,
    )
```

- [ ] **Step 2: Add tests for defaults, overrides, and invalid ranges**

```python
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
```

- [ ] **Step 3: Add source-contract assertions**

Assert that the non-CAD launch path contains `--autoscale`, that the fixed `--concurrency` path remains available for other queues, and that `status.sh` checks the expected classification autoscale command.

- [ ] **Step 4: Run the focused tests and confirm RED**

Run:

```bash
cd backend
uv run pytest tests/infrastructure/test_scripts.py \
  -k "classification_worker_autoscale" -q
```

Expected: failures because the topology still contains `dxf_classification|1|...` and invalid values are not rejected.

### Task 2: Implement local autoscale and runtime verification

**Files:**
- Modify: `scripts/lib/cad_worker.sh`
- Modify: `scripts/status.sh`
- Test: `backend/tests/infrastructure/test_scripts.py`

- [ ] **Step 1: Define and validate the range**

Add defaults:

```bash
DXF_CLASSIFICATION_AUTOSCALE_MIN="${DXF_CLASSIFICATION_AUTOSCALE_MIN:-1}"
DXF_CLASSIFICATION_AUTOSCALE_MAX="${DXF_CLASSIFICATION_AUTOSCALE_MAX:-3}"
```

Add a validation function that accepts only positive integers and requires `MIN <= MAX`. If validation fails while the library is sourced, print `DXF classification autoscale 配置无效` to stderr and return a non-zero status.

- [ ] **Step 2: Extend the worker topology**

Use six fields:

```bash
# queue|capacity|slug|optional-display|optional-autoscale-min|optional-autoscale-max
"dxf_classification|${DXF_CLASSIFICATION_AUTOSCALE_MAX}|dxf-classification||${DXF_CLASSIFICATION_AUTOSCALE_MIN}|${DXF_CLASSIFICATION_AUTOSCALE_MAX}"
```

Update every `WORKER_SPECS` reader to consume all six fields without changing the existing fixed-concurrency workers.

- [ ] **Step 3: Launch the classification worker with autoscale**

For specs containing both autoscale fields, pass:

```bash
--autoscale="${autoscale_max},${autoscale_min}"
```

Do not also pass `--concurrency`. Export `DWG_WORKER_CONCURRENCY` as the maximum capacity and `DWG_WORKER_AUTOSCALE` as `MIN-MAX` for worker telemetry. Keep the existing `--concurrency` argument for specs without autoscale metadata.

- [ ] **Step 4: Verify the live command in status**

For the classification spec, inspect the parent worker process arguments and require the exact `--autoscale=MAX,MIN` token. Report `autoscale=MIN-MAX`; mark `ALL_OK=false` if a stale fixed-concurrency worker is running.

- [ ] **Step 5: Run the focused tests and confirm GREEN**

Run:

```bash
cd backend
uv run pytest tests/infrastructure/test_scripts.py \
  -k "classification_worker_autoscale or start_stop_status_scripts" -q
```

Expected: all selected tests pass.

### Task 3: Keep Compose behavior consistent

**Files:**
- Modify: `backend/tests/infrastructure/test_compose.py`
- Modify: `compose.yaml`

- [ ] **Step 1: Write a failing Compose test**

```python
def test_classification_worker_uses_configurable_autoscale(self):
    service = _load()["services"]["worker-dxf-classification"]
    command = service["command"]
    assert "--autoscale=${DXF_CLASSIFICATION_AUTOSCALE_MAX:-3},${DXF_CLASSIFICATION_AUTOSCALE_MIN:-1}" in command
    assert "--concurrency=1" not in command
    assert service["environment"]["DWG_WORKER_CONCURRENCY"] == "${DXF_CLASSIFICATION_AUTOSCALE_MAX:-3}"
    assert service["environment"]["DWG_WORKER_AUTOSCALE"] == "${DXF_CLASSIFICATION_AUTOSCALE_MIN:-1}-${DXF_CLASSIFICATION_AUTOSCALE_MAX:-3}"
```

- [ ] **Step 2: Run the test and confirm RED**

Run:

```bash
cd backend
uv run pytest tests/infrastructure/test_compose.py \
  -k "classification_worker_uses_configurable_autoscale" -q
```

Expected: failure because Compose still uses `--concurrency=1`.

- [ ] **Step 3: Update the Compose worker**

Replace fixed classification concurrency with the shared autoscale variables and publish maximum capacity plus range through the worker environment.

- [ ] **Step 4: Run the Compose test and confirm GREEN**

Run the focused command from Step 2 and expect one passing test.

### Task 4: Publish the operator configuration

**Files:**
- Modify: `.env.example`
- Modify: `.env.docker.example`
- Modify: `docs/reference/configuration.md`
- Modify: `docs/guides/deployment.md`

- [ ] **Step 1: Add defaults to both environment templates**

```dotenv
DXF_CLASSIFICATION_AUTOSCALE_MIN=1
DXF_CLASSIFICATION_AUTOSCALE_MAX=3
```

- [ ] **Step 2: Document semantics**

State that the values control concurrent project-level Jobs, not parallel files inside one project; `prefetch=1` preserves fairness; and the deployment machine must be load-tested before increasing the maximum.

- [ ] **Step 3: Check generated documentation inputs**

Run:

```bash
rg -n "DXF_CLASSIFICATION_AUTOSCALE_(MIN|MAX)" \
  .env.example .env.docker.example docs/reference/configuration.md docs/guides/deployment.md
```

Expected: both variables appear in all four files.

### Task 5: Regression, live restart, and release

**Files:**
- Verify: `backend/tests/infrastructure/test_scripts.py`
- Verify: `backend/tests/infrastructure/test_compose.py`
- Verify: `backend/tests/dxf_classification/test_dxf_classification_pipeline.py`
- Verify: `backend/tests/workflows/test_workflow_dxf_contracts.py`
- Verify: `backend/tests/workflows/test_workflow_production.py`

- [ ] **Step 1: Run the regression suite**

```bash
cd backend
uv run pytest \
  tests/infrastructure/test_scripts.py \
  tests/infrastructure/test_compose.py \
  tests/dxf_classification/test_dxf_classification_pipeline.py \
  tests/workflows/test_workflow_dxf_contracts.py \
  tests/workflows/test_workflow_production.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run formatting and repository checks**

```bash
git diff --check
bash -n scripts/lib/cad_worker.sh scripts/status.sh
```

Expected: both commands exit zero.

- [ ] **Step 3: Restart the full managed stack**

```bash
bash scripts/start-all.sh
bash scripts/status.sh
```

Expected: all old processes stop; the classification worker starts with `autoscale=1-3`; all readiness checks pass.

- [ ] **Step 4: Verify the runtime process**

Inspect the classification worker parent command and require `--autoscale=3,1`. Confirm that the idle pool contains one child process.

- [ ] **Step 5: Commit only autoscale-owned files**

```bash
git add \
  scripts/lib/cad_worker.sh scripts/status.sh compose.yaml \
  .env.example .env.docker.example \
  docs/reference/configuration.md docs/guides/deployment.md \
  backend/tests/infrastructure/test_scripts.py \
  backend/tests/infrastructure/test_compose.py
git commit -m "feat(classification): autoscale concurrent project workers"
```

- [ ] **Step 6: Push and verify**

```bash
git push origin main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
git ls-remote --heads origin main
```

Expected: local `HEAD`, local `origin/main`, and remote `main` resolve to the same commit.
