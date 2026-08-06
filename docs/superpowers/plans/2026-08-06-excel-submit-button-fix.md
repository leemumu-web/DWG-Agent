# Excel 提交按钮最小修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the Excel Final submit button from appearing clickable while silently returning when the idempotency key cannot be generated.

**Architecture:** Keep the fix inside the existing Excel Final page. Add a small request-key helper with browser capability fallbacks, pass the key state into the existing upload action component, and preserve the current upload API and task flow.

**Tech Stack:** React 19, TypeScript, Ant Design, Playwright.

## Global Constraints

- Modify only the Excel Final frontend page/component and focused frontend tests.
- Do not change backend endpoints, database behavior, task dispatch, polling, retry, or download flows.
- Keep `Idempotency-Key` generation per file selection and do not reuse keys across new selections.
- Do not add dependencies.

---

### Task 1: Harden Excel request-key state and submit feedback

**Files:**
- Create: `frontend/src/features/excel-processing/model/requestKey.ts`
- Modify: `frontend/src/features/excel-processing/ExcelFinalPage.tsx`
- Modify: `frontend/src/features/excel-processing/components/ExcelFinalUploadActions.tsx`
- Test: `frontend/tests/e2e/excel-processing/excel-final-flow.spec.ts`

**Interfaces:**
- `ExcelFinalPage` continues calling `uploadAndProcessExcel(file, requestKey)` with the same API contract.
- `ExcelFinalUploadActions` receives a new `requestKeyAvailable: boolean` prop and uses it only for button availability.

- [ ] **Step 1: Add a request-key helper and test fixture control**

Add `createRequestKey()` in `model/requestKey.ts` and import it into `ExcelFinalPage.tsx`. The helper returns a backend-safe key. It must prefer `globalThis.crypto.randomUUID`, then use `getRandomValues`, then use a timestamp/random fallback containing only `[A-Za-z0-9._:-]` characters.

Add an end-to-end test that stubs the browser crypto object before file selection and verifies the upload still sends a POST with an `Idempotency-Key` header.

- [ ] **Step 2: Prevent invalid submit state**

Pass `Boolean(selectedRequestKey)` to `ExcelFinalUploadActions` as `requestKeyAvailable`. Change the button disabled expression to:

```tsx
disabled={!file || !requestKeyAvailable}
```

Add a visible message in `submit()` for the impossible-but-recoverable missing-state case before returning.

- [ ] **Step 3: Run focused validation**

Run from `E:\桌面\DWG-Agent\frontend`:

```powershell
npm run build
npx playwright test tests/e2e/excel-processing/excel-final-flow.spec.ts
```

Expected result: TypeScript/Vite build succeeds and the Excel Final flow tests pass.

- [ ] **Step 4: Review the diff and commit**

Run:

```powershell
git diff --check
git status --short
git diff -- frontend/src/features/excel-processing/ExcelFinalPage.tsx frontend/src/features/excel-processing/components/ExcelFinalUploadActions.tsx frontend/tests/e2e/excel-processing/excel-final-flow.spec.ts
```

Commit only the focused fix and test changes with:

```powershell
git add frontend/src/features/excel-processing/model/requestKey.ts frontend/src/features/excel-processing/ExcelFinalPage.tsx frontend/src/features/excel-processing/components/ExcelFinalUploadActions.tsx frontend/tests/e2e/excel-processing/excel-final-flow.spec.ts
git commit -m "fix(frontend): handle Excel submit request key fallback"
```
