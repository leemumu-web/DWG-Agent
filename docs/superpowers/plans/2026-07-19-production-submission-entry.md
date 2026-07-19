# Production Submission Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make production batch submission visible from an empty workflow page and enter DWG/Excel upload immediately after creation.

**Architecture:** Keep all backend contracts unchanged. Compose existing create and start APIs in one frontend mutation, then reuse `ProductionInputPanel`; render an in-body recovery action for draft workflows.

**Tech Stack:** React 19, TypeScript 6, Ant Design, React Query, pytest frontend source contracts, Playwright.

---

### Task 1: Lock the visible submission contract

**Files:**
- Modify: `backend/tests/test_frontend_contract.py`

- [ ] Add assertions for `提交生产批次`, `创建并进入上传`, sequential `createWorkflow`/`startWorkflow`, and `启动并进入上传` draft recovery.
- [ ] Run `backend/.venv/bin/pytest -q backend/tests/test_frontend_contract.py -k production_submission_entry` and confirm it fails before implementation.

### Task 2: Compose create and start in the primary entry

**Files:**
- Modify: `frontend/src/features/workflows/WorkflowsPage.tsx`

- [ ] Rename the page and drawer actions to production submission language.
- [ ] Fix the submitted workflow type to `linux_production` and call `startWorkflow(created.id)` after `createWorkflow`.
- [ ] On success close the form, open workflow detail and invalidate list/detail queries.
- [ ] If start fails, keep the created draft discoverable and show the backend error.
- [ ] Render an in-body `启动并进入上传` action for every Linux draft.

### Task 3: Verify browser behavior and publish

**Files:**
- Modify: `frontend/tests/e2e/workflow-input.spec.ts`
- Modify: `docs/workflow-framework.md`

- [ ] Extend the mocked workflow scenario to prove the primary action is visible on an empty/list page and the draft recovery action exists.
- [ ] Run the frontend contract, `npm run build`, and the workflow Playwright test.
- [ ] Update the frontend workflow documentation, commit the implementation, rebuild the served static bundle, and verify Nginx status.

