# Remove Excel Enhanced Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the LuckyExcel enhanced preview path and its static asset while preserving the server-backed Excel preview.

**Architecture:** `ExcelPreview.tsx` becomes a single-mode component backed only by `fetchExcelPreview`. `excelPreviewModel.tsx` retains reusable fast-table presentation helpers and drops all LuckyExcel data conversion and browser-global types.

**Tech Stack:** React, TypeScript, Ant Design, Vite, Pytest source-contract tests.

---

## Task 1: Add a failing removal contract

**Files:**
- Create: `backend/tests/contracts/test_excel_preview_source.py`
- Test: `backend/tests/contracts/test_excel_preview_source.py`

- [ ] **Step 1: Write the source contract**

```python
from tests.support.paths import REPO_ROOT


def test_excel_preview_uses_only_server_backed_fast_preview():
    component = (
        REPO_ROOT / "frontend/src/features/excel-processing/ExcelPreview.tsx"
    ).read_text(encoding="utf-8")
    model = (
        REPO_ROOT
        / "frontend/src/features/excel-processing/model/excelPreviewModel.tsx"
    ).read_text(encoding="utf-8")

    assert "fetchExcelPreview" in component
    assert "downloadFile" in component
    for removed in (
        "增强预览",
        "LuckyExcel",
        "getFileDownloadUrl",
        "apiClient",
        "loadEnhanced",
        "PreviewMode",
    ):
        assert removed not in component
    assert "Lucky" not in model
    assert not (REPO_ROOT / "frontend/public/luckyexcel.umd.js").exists()
```

- [ ] **Step 2: Run the test and confirm RED**

Run:

```bash
cd backend
uv run pytest tests/contracts/test_excel_preview_source.py -q
```

Expected: failure because the component, model, and public asset still contain LuckyExcel.

## Task 2: Reduce the component to fast preview

**Files:**
- Modify: `frontend/src/features/excel-processing/ExcelPreview.tsx`
- Test: `backend/tests/contracts/test_excel_preview_source.py`

- [ ] **Step 1: Remove enhanced imports and state**

Keep React hooks `useState`, `useCallback`, `useMemo`, `useEffect`, and `useRef`. Remove `Segmented`, `Tooltip`, `apiClient`, `getFileDownloadUrl`, Lucky builders, Lucky types, mode state, enhanced loading state, and script state.

- [ ] **Step 2: Remove enhanced loading and rendering**

Delete script injection, signed-download/Blob processing, enhanced effects, Lucky table construction, mode-specific branches, enhanced error fallback, and enhanced table JSX.

- [ ] **Step 3: Make shared state fast-only**

Use:

```tsx
const isLoading = fastLoading;
const sheetCount = data?.sheets.length || 0;
const totalRows = data?.total_rows || 0;
const displayName = data?.file || fileName;
```

Refresh must call only `loadFast(fileId)`. The information bar must display the current sheet without a mode selector.

- [ ] **Step 4: Preserve the fast table**

Keep sheet tabs, `buildFastColumns`, summary-row styling, pagination disabled, sticky header, download, refresh, close, loading skeleton, and empty state.

## Task 3: Remove the Lucky model and asset

**Files:**
- Modify: `frontend/src/features/excel-processing/model/excelPreviewModel.tsx`
- Delete: `frontend/public/luckyexcel.umd.js`
- Test: `backend/tests/contracts/test_excel_preview_source.py`

- [ ] **Step 1: Delete Lucky-only model declarations**

Remove `LuckyCell`, `LuckySheetConfig`, `LuckySheet`, `LuckyExportJson`, the global `Window.LuckyExcel` declaration, `PreviewMode`, `LuckyTableModel`, `buildLuckyTable`, and `buildLuckyColumns`.

- [ ] **Step 2: Keep fast presentation helpers**

Retain `NUMERIC_COLS`, `SUMMARY_MARKERS`, `isSummaryRow`, `computeColWidth`, `cellRender`, and `buildFastColumns`.

- [ ] **Step 3: Delete the public script**

Delete `frontend/public/luckyexcel.umd.js`. Do not edit generated `frontend/dist`; the production build recreates it from `public`.

- [ ] **Step 4: Run the focused contract and confirm GREEN**

Run:

```bash
cd backend
uv run pytest tests/contracts/test_excel_preview_source.py -q
```

Expected: one passing test.

### Task 4: Synchronize documentation

**Files:**
- Modify: `frontend/src/features/excel-processing/README.md`
- Modify: `frontend/src/features/excel-processing/model/README.md`

- [ ] **Step 1: Describe the fast-only component**

State that `ExcelPreview.tsx` uses the backend preview contract and supports sheet switching, refresh, and download.

- [ ] **Step 2: Describe the fast-only model**

State that `excelPreviewModel.tsx` converts backend preview headers and rows into table columns and display formatting.

- [ ] **Step 3: Verify removal text**

Run:

```bash
rg -n "增强预览|LuckyExcel|luckyexcel" \
  frontend/src/features/excel-processing frontend/public
```

Expected: no matches.

### Task 5: Build, restart, and release

**Files:**
- Verify: `frontend/src/features/excel-processing/ExcelPreview.tsx`
- Verify: `frontend/src/features/excel-processing/model/excelPreviewModel.tsx`
- Verify: `backend/tests/contracts/test_excel_preview_source.py`

- [ ] **Step 1: Run focused tests and the production build**

```bash
cd backend
uv run pytest tests/contracts/test_excel_preview_source.py -q
cd ../frontend
npm run build
```

Expected: test passes; TypeScript and Vite production build complete successfully.

- [ ] **Step 2: Confirm the built asset is absent**

```bash
test ! -e frontend/dist/luckyexcel.umd.js
```

Expected: exit zero.

- [ ] **Step 3: Restart and verify the managed stack**

```bash
bash scripts/start-all.sh
bash scripts/status.sh
```

Expected: all services healthy, frontend build current, gateway health and SPA checks pass.

- [ ] **Step 4: Commit only removal-owned files**

```bash
git add \
  frontend/src/features/excel-processing/ExcelPreview.tsx \
  frontend/src/features/excel-processing/model/excelPreviewModel.tsx \
  frontend/src/features/excel-processing/README.md \
  frontend/src/features/excel-processing/model/README.md \
  frontend/public/luckyexcel.umd.js \
  backend/tests/contracts/test_excel_preview_source.py
git commit -m "refactor(excel): remove enhanced preview"
```

- [ ] **Step 5: Push and verify**

```bash
git push origin main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
git ls-remote --heads origin main
```

Expected: local, tracking, and remote `main` SHAs match.
