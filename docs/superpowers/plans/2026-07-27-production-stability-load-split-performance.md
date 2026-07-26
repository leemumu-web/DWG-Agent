# Production Stability, Load, and Split Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven development and inline execution. The project owner explicitly prohibits subagents.

**Goal:** Build repeatable production load evidence, find stability or throughput defects, and tune multi-project DXF splitting without weakening data or permission contracts.

**Architecture:** A local HTTP driver creates isolated real production workflows for four accounts and validates every stage and artifact count. A server-local sampler records Docker, OS, MySQL, and queue pressure; JSON reports join the two timelines without embedding SSH credentials.

**Tech Stack:** Python 3.12, httpx, FastAPI contracts, Celery/MySQL transport, Docker Compose, pytest, agent-browser.

---

## Task 1: Lock load-tool contracts

**Files:**
- Create: `backend/tests/infrastructure/test_production_load_tools.py`
- Create: `scripts/production/__init__.py`

- [ ] Write failing tests for positive concurrency parsing, percentile calculation, secret redaction, stage-count conservation and non-zero exit on any failed scenario.
- [ ] Run `cd backend && uv run pytest -q tests/infrastructure/test_production_load_tools.py` and confirm collection fails because `scripts.production.workflow_load` does not exist.
- [ ] Record the failure output before adding production code.

## Task 2: Implement the real HTTP workflow driver

**Files:**
- Create: `scripts/production/workflow_load.py`
- Modify: `backend/tests/infrastructure/test_production_load_tools.py`

- [ ] Implement environment-only credentials, paced login, unique project prefixes and bounded concurrency.
- [ ] Implement the exact API sequence for project creation, input batch, Excel upload, DWG folder upload, conversion polling, freeze, classification execution/polling, split execution/polling and archive download.
- [ ] Capture request ID, HTTP status, Job/attempt, stage duration, errors and input/output counts without storing tokens or passwords.
- [ ] Reject archive path traversal, duplicate ZIP names, missing reports and any count-conservation violation.
- [ ] Emit one JSON report and return non-zero if any project fails.
- [ ] Run the focused test until it passes.

## Task 3: Add server-local resource sampling

**Files:**
- Modify: `backend/tests/infrastructure/test_production_load_tools.py`
- Create: `scripts/production/resource_sampler.py`
- Create: `scripts/production/README.md`

- [ ] Write failing tests for Docker-stat parsing, monotonic timestamps, bounded sampling intervals and summary peak calculations.
- [ ] Run the focused test and confirm the new sampler tests fail for the expected missing implementation.
- [ ] Implement JSONL sampling for load average, CPU, memory, swap, block I/O, Docker health/restarts/OOM, MySQL connections and Job/queue state.
- [ ] Ensure the sampler is read-only, has no SSH logic and exits cleanly on SIGINT/SIGTERM.
- [ ] Document exact environment variables, fixture rules, gradual load matrix, report interpretation and precise cleanup boundary.

## Task 4: Verify the test tools before production load

**Files:**
- Modify only files required by failures proven above.

- [ ] Run `cd backend && uv run pytest -q tests/infrastructure/test_production_load_tools.py tests/infrastructure/test_scripts.py tests/infrastructure/test_compose.py`.
- [ ] Run `bash scripts/verify.sh quick`.
- [ ] Run both tools with `--help` and a local invalid configuration; confirm messages are Chinese, secrets are absent and exit codes are non-zero.
- [ ] Commit the test tools, docs and design artifacts.

## Task 5: Execute single-project and four-account baseline

**Files:**
- No tracked modifications unless a reproducible failure is found.

- [ ] Record server release, container IDs, restart/OOM counts and a log timestamp boundary.
- [ ] Select a real Excel and a controlled DWG fixture set; record sizes and SHA-256.
- [ ] Run one full workflow and verify every stage, archive and quantity invariant.
- [ ] Run 1, 2 and 4 concurrent projects across the four accounts while the resource sampler is active.
- [ ] Use agent-browser for one complete operator-visible workflow and verify Chinese progress, errors, download and refresh behavior.
- [ ] Classify every failure by component boundary and reproduce it before proposing a fix.

## Task 6: Benchmark and tune split concurrency

**Files:**
- Modify configuration/tests/docs only if measurement proves a better safe value.

- [ ] With identical inputs, run split-worker concurrency 1, 2 and 4 for at least two rounds each.
- [ ] Compare throughput, p50/p95, CPU, I/O wait, MySQL connections, memory, swap, restarts, OOM and count conservation.
- [ ] If 4 is stable and materially faster, test 6 once; otherwise stop at the best lower value.
- [ ] Write a failing configuration-contract test before changing any repository default or validation.
- [ ] Apply the smallest proven tuning, rerun focused tests and document the hardware-specific server override separately from conservative defaults.

## Task 7: Fix reproduced defects with TDD

**Files:**
- Determined only after root-cause evidence.

- [ ] For each defect, add the smallest test that reproduces the exact error and watch it fail.
- [ ] Trace the error through Nginx, API, transaction, queue, worker, algorithm and persistence boundaries.
- [ ] Implement one root-cause fix at a time and rerun the focused test after each change.
- [ ] Run backend, infrastructure, stage and frontend regressions affected by the change.
- [ ] Self-review the diff because subagents are prohibited.

## Task 8: Rebuild, deploy, and repeat the same matrix

**Files:**
- Modify release documentation only if runtime contracts changed.

- [ ] Run the full repository release gates and record exact counts.
- [ ] Build a new encrypted server bundle with a one-time server key and verify outer and inner checksums.
- [ ] Upload through SSH, preserve `.env.docker`, deploy, and prove 14/14 healthy with zero non-zero restarts.
- [ ] Repeat single-project, four-account, split-concurrency and 30-minute soak scenarios using the same fixture hashes.
- [ ] Perform a controlled reboot while no Jobs are active and verify dependency waiting and recovery.
- [ ] Precisely remove test workflows/files/objects, old release tags, upload cache and the one-time private key.
- [ ] Confirm business rows and MinIO test objects are zero while users, roles and handbook remain intact.
- [ ] Commit and push all validated changes; verify `HEAD == origin/main`.
