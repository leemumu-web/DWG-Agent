# Production Batch Entry UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make production batch creation self-explanatory and keep the primary action next to the form.

**Architecture:** Keep the existing one-drawer workflow and API calls. Add presentation state for an editable suggested name, restructure only the pre-creation drawer content, and preserve the existing post-creation upload panel.

**Tech Stack:** React 19, TypeScript, Ant Design, Playwright.

---

### Task 1: Lock the interaction contract

**Files:**
- Modify: `frontend/tests/e2e/workflow-input.spec.ts`

- [ ] Assert the page exposes “新建并上传生产批次”.
- [ ] Assert selecting a project suggests `P7-20260719-生产批次`.
- [ ] Assert the in-form button says “创建批次，下一步上传文件”.
- [ ] Run `npx playwright test tests/e2e/workflow-input.spec.ts` and confirm it fails on the old labels.

### Task 2: Implement the guided create surface

**Files:**
- Modify: `frontend/src/features/workflows/WorkflowsPage.tsx`
- Modify: `frontend/src/styles.css`

- [ ] Track whether the operator manually edited the name.
- [ ] Suggest the deterministic name when a project is selected.
- [ ] Add the three-step expectation, preparation checklist, and form-local primary action.
- [ ] Disable dismiss and inputs while creation is pending.
- [ ] Add responsive styles scoped to `.production-create-*`.
- [ ] Re-run the focused Playwright test and confirm it passes.

### Task 3: Verify and publish

**Files:**
- Modify: `backend/tests/test_frontend_contract.py`

- [ ] Update static frontend wording assertions.
- [ ] Run frontend contract, production build, focused Playwright, Ruff, and docs checks.
- [ ] Rebuild the served frontend and verify `scripts/status.sh` is healthy.
- [ ] Commit the implementation and synchronized documentation.
