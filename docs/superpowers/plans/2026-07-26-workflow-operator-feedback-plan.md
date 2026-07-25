# Workflow Operator Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven development and inline execution. The project owner explicitly prohibits subagents.

**Goal:** Give production operators persistent, actionable workflow errors and recoverable Stage A3 downloads.

**Architecture:** Extend the existing safe API error parser with status-aware recovery guidance, render it through one shared alert component, and keep download state inside each export control. Preserve the native streaming and server-side file contracts.

**Tech Stack:** React 19, TypeScript 6, Ant Design 6, TanStack Query 5, Playwright, pytest contract tests.

---

## Task 1: Lock operator-visible behavior

**Files:**
- Modify: `frontend/tests/e2e/workflows/workflow-detail.spec.ts`
- Modify: `backend/tests/contracts/test_frontend_contract.py`

- [x] Add a Playwright case where selective preview first returns a structured 503 error containing `error.code`, `error.message` and `meta.request_id`; assert that the dialog shows the message, code, request ID, recovery advice and a retry button.
- [x] Make the retry return a successful preview and assert that selectable categories appear.
- [x] Change the selective-create assertion so the modal remains open after the native download starts and exposes `再次开始下载` plus a close action.
- [x] Add an empty-preview assertion that requires `当前没有可导出的文件`.
- [x] Run the focused test and confirm it fails because those UI elements do not exist yet:

```bash
cd frontend
npx playwright test tests/e2e/workflows/workflow-detail.spec.ts --grep "operator guidance"
```

## Task 2: Add one safe error presentation boundary

**Files:**
- Modify: `frontend/src/shared/api/error.ts`
- Modify: `frontend/src/shared/api/index.ts`
- Create: `frontend/src/shared/components/ApiErrorAlert.tsx`
- Modify: `frontend/src/shared/components/index.ts`
- Modify: `frontend/src/shared/api/README.md`
- Modify: `frontend/src/shared/components/README.md`

- [x] Add `status?: number` to `ParsedApiError`, set it only from Axios responses, and export `apiErrorRecovery(parsed)`.
- [x] Map authentication, authorization, missing resource, conflict, size/type/validation, rate limit, server, timeout and network failures to bounded Chinese recovery advice.
- [x] Implement `ApiErrorAlert` with `title`, `error`, `fallback`, optional `onRetry`, `retryLabel` and `retryLoading`; render a persistent Ant Design error alert with the parsed message, `处理建议：...`, and a retry action.
- [x] Run the focused contract test and Playwright case until the new shared behavior passes.

## Task 3: Make Stage A3 exports self-explanatory and recoverable

**Files:**
- Modify: `frontend/src/features/workflows/DrawingSelectiveExportControl.tsx`
- Modify: `frontend/src/features/workflows/WorkflowBatchExportControl.tsx`
- Modify: `frontend/src/features/workflows/DrawingProcessingExportActions.tsx`
- Modify: `frontend/src/features/workflows/DrawingProcessingPanel.tsx`
- Modify: `frontend/src/features/workflows/styles.css`
- Modify: `frontend/src/features/workflows/README.md`

- [x] Rename the controls to `分类图纸导出` and `分批导出并清理`; wrap disabled controls in a tooltip containing the computed reason.
- [x] Keep the selective modal open after create, store the prepared export, show file count, size, name and expiry, and provide `再次开始下载` and `下载已开始，关闭` actions.
- [x] If no preview categories are available, render a warning explaining that the current run has no matching source DXF.
- [x] Catch native download launch errors in both controls and keep all server files untouched.
- [x] Replace run/preview/status error blocks with `ApiErrorAlert` while retaining each request's retry callback.
- [x] Run the focused workflow E2E and confirm all assertions pass.

## Task 4: Verify and release

**Files:**
- Modify only files required by failures proven in the preceding tests.

- [x] Run `cd frontend && npm run build` and expect architecture check, TypeScript and Vite build to pass.
- [x] Run `cd frontend && npx playwright test tests/e2e/workflows` and expect all workflow cases to pass.
- [x] Run `cd backend && uv run pytest -q tests/contracts/test_frontend_contract.py` and expect all contract cases to pass.
- [x] Run `bash scripts/verify.sh quick` and expect zero failed gates.
- [x] Inspect the production entry with `agent-browser` at `http://127.0.0.1:8080`, checking desktop and narrow viewport labels, focus order, errors and retry actions.
- [x] Commit only tracked implementation, tests and documentation; push `main`; verify local `HEAD` equals `origin/main`.
