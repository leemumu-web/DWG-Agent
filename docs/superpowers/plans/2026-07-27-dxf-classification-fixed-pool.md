# DXF Classification Fixed Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the production classification worker's failure-prone prefork autoscaling with one configurable fixed process pool.

**Architecture:** Keep the dedicated `dxf_classification` queue and worker service, but create all execution processes at worker startup with `--concurrency=N`. Use one positive-integer setting across local scripts, Compose, telemetry, status checks, tests, and operator documentation.

**Tech Stack:** Bash, Celery prefork, Docker Compose, Pytest, Markdown

---

## Tasks

### Task 1: Lock the fixed-pool contract

**Files:**
- Modify: `backend/tests/infrastructure/test_scripts.py`
- Modify: `backend/tests/infrastructure/test_compose.py`

- [x] **Step 1: Replace autoscale expectations with fixed-pool expectations**

Require the default topology to contain `dxf_classification|3|dxf-classification`, accept `DXF_CLASSIFICATION_WORKER_CONCURRENCY=4`, reject zero and non-numeric values, and require Compose to contain `--concurrency=${DXF_CLASSIFICATION_WORKER_CONCURRENCY:-3}` without `--autoscale`.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run --project backend pytest \
  backend/tests/infrastructure/test_scripts.py \
  backend/tests/infrastructure/test_compose.py \
  -k "classification_worker" -q
```

Expected: failures show the runtime still exposes `DXF_CLASSIFICATION_AUTOSCALE_MIN/MAX` and launches Celery with `--autoscale`.

### Task 2: Implement the fixed worker topology

**Files:**
- Modify: `scripts/lib/cad_worker.sh`
- Modify: `scripts/status.sh`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Modify: `.env.docker.example`

- [x] **Step 1: Replace autoscale variables and validation**

Define `DXF_CLASSIFICATION_WORKER_CONCURRENCY="${DXF_CLASSIFICATION_WORKER_CONCURRENCY:-3}"` and reject any value that is not a positive integer.

- [x] **Step 2: Simplify the topology and launcher**

Represent classification like other fixed queues, pass only `--concurrency=N`, export only `DWG_WORKER_CONCURRENCY=N`, and remove the obsolete autoscale parsing and status branches.

- [x] **Step 3: Update Compose and example environments**

Use the same fixed concurrency variable in the service command and environment. Replace both old autoscale settings in each example environment with the new single setting.

- [x] **Step 4: Run the focused tests and verify GREEN**

Run the Task 1 pytest command.

Expected: all selected tests pass.

### Task 3: Synchronize operator documentation

**Files:**
- Modify: `docs/guides/deployment.md`
- Modify: `docs/reference/configuration.md`

- [x] **Step 1: Document the stable fixed capacity**

Describe `DXF_CLASSIFICATION_WORKER_CONCURRENCY=3` as the number of projects that may classify concurrently and explain that a restart is required after changes.

- [x] **Step 2: Remove current documentation claims that classification scales down to one process**

Run:

```bash
rg -n "DXF_CLASSIFICATION_AUTOSCALE|分类.*自动伸缩" \
  .env.example .env.docker.example compose.yaml scripts docs/guides docs/reference
```

Expected: no current runtime or operator documentation references remain.

### Task 4: Verify and release

**Files:**
- Verify only: repository gates and production server

- [x] **Step 1: Run infrastructure and quick gates**

Run:

```bash
uv run --project backend pytest backend/tests/infrastructure -q
bash scripts/verify.sh quick
```

Expected: both commands pass.

- [x] **Step 2: Commit the fixed-pool change**

Commit the implementation, tests, design, plan, and documentation as one root-cause fix.

- [x] **Step 3: Build a new encrypted release and deploy**

Use the one-time GPG release flow, set the server's fixed classification concurrency to 4, deploy all 14 services, run readiness and protected remnant smoke checks, and destroy the temporary private key at both ends.

- [x] **Step 4: Repeat two-account production validation**

Run two consecutive 40-DWG workflows through the public HTTP API. Require conversion, classification, split, and official ZIP download to succeed for both accounts, with zero restart/OOM evidence and clean split work directories.
