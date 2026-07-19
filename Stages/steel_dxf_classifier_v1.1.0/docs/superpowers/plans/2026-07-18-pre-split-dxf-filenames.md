# Pre-Split DXF Filename Preprocessing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transactionally rename every first-level input DXF to `*_拆板前.dxf` before classification, migrate the two real inputs, and rebuild verified release artifacts.

**Architecture:** A new focused `preprocess.py` module owns filename planning, collision detection and rollback. `batch.classify_directory()` calls it after input/output preflight and before DXF parsing; the existing classifier and routing pipeline then naturally use normalized names.

**Tech Stack:** Python 3.13, pathlib/os, pytest, ezdxf, uv, Info-ZIP.

---

### Task 1: Transactional filename preprocessor

**Files:**
- Create: `src/steel_dxf_classifier/preprocess.py`
- Create: `tests/test_preprocess.py`

- [ ] **Step 1: Write failing tests for normalization and idempotence**

Create tests that build `A.dxf`, `B.DXF`, `C_拆板前.dxf`, a nested `nested/N.dxf`, and `note.txt`; call `preprocess_dxf_filenames(tmp_path)` and assert the first level is exactly `A_拆板前.dxf`, `B_拆板前.dxf`, `C_拆板前.dxf`, with nested/non-DXF files untouched and byte contents unchanged. Call it twice and assert the second result is identical.

- [ ] **Step 2: Write failing collision and rollback tests**

Add a collision case containing `A.dxf` and `A_拆板前.dxf`; assert `FilenamePreprocessError` and unchanged names. Monkeypatch `os.replace` to fail during the second-stage promotion and assert all original names and contents are restored with no hidden temporary files.

- [ ] **Step 3: Verify the new tests fail because the API is absent**

Run: `uv run pytest -q tests/test_preprocess.py`

Expected: collection fails because `steel_dxf_classifier.preprocess` does not exist.

- [ ] **Step 4: Implement the focused preprocessor**

Create:

```python
PRE_SPLIT_SUFFIX = "_拆板前"

class FilenamePreprocessError(RuntimeError):
    pass

def preprocess_dxf_filenames(directory: str | Path) -> tuple[Path, ...]:
    """Normalize first-level DXF names transactionally and return sorted paths."""
```

The function must scan only files with `.dxf` case-insensitively, map them to lowercase `.dxf`, avoid a second suffix, reject casefolded destination collisions before mutation, stage all renames through UUID-based hidden names, and roll back both promoted and staged entries on any `OSError`.

- [ ] **Step 5: Verify preprocessor tests pass**

Run: `uv run pytest -q tests/test_preprocess.py`

Expected: all preprocessor tests pass.

- [ ] **Step 6: Commit the isolated component**

```bash
git add src/steel_dxf_classifier/preprocess.py tests/test_preprocess.py
git commit -m "feat: preprocess DXF names transactionally"
```

### Task 2: Integrate preprocessing into classification

**Files:**
- Modify: `src/steel_dxf_classifier/batch.py`
- Modify: `tests/test_batch.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write a failing batch integration test**

Create `part.dxf`, run `classify_directory()`, then assert the input became `part_拆板前.dxf`, the route contains `part_拆板前.dxf`, the old name is absent, and the report uses `source_name=part_拆板前.dxf`.

- [ ] **Step 2: Verify the integration test fails**

Run the new test directly with `uv run pytest -q tests/test_batch.py::<test-name>`.

Expected: the input and routed copy still use `part.dxf`.

- [ ] **Step 3: Call the preprocessor before parsing**

Import `preprocess_dxf_filenames` in `batch.py` and replace `_input_files(source)` with `preprocess_dxf_filenames(source)` after existing-output preflight. Keep source ordering deterministic.

- [ ] **Step 4: Update existing behavior assertions**

Change batch/CLI test expectations from names such as `bh.dxf`, `part.dxf`, and `零件一.dxf` to their `_拆板前.dxf` forms. Replace the old source-name-map equality assertion with byte-digest assertions proving contents are unchanged despite filenames changing.

- [ ] **Step 5: Verify all integration tests pass**

Run: `uv run pytest -q tests/test_batch.py tests/test_cli.py tests/test_preprocess.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit classification integration**

```bash
git add src/steel_dxf_classifier/batch.py tests/test_batch.py tests/test_cli.py
git commit -m "feat: preprocess DXF names before classification"
```

### Task 3: Synchronize operator documentation

**Files:**
- Modify: `tests/test_documentation.py`
- Modify: `README.md`
- Modify: `docs/CLASSIFICATION_RULES.md`
- Modify later after real run: `docs/VALIDATION.md`

- [ ] **Step 1: Add a failing documentation contract**

Require the terms `_拆板前.dxf`, `原地重命名`, `重复运行`, `命名冲突`, and `内容保持不变` across operator documents.

- [ ] **Step 2: Verify the documentation test fails**

Run: `uv run pytest -q tests/test_documentation.py`

Expected: failure shows the new preprocessing contract is absent.

- [ ] **Step 3: Update README and classification rules**

Document automatic first-level renaming, idempotence, whole-batch collision failure, report/output naming, and that only filenames—not DXF bytes—change. Remove claims that input filenames are never modified.

- [ ] **Step 4: Verify documentation tests pass**

Run: `uv run pytest -q tests/test_documentation.py`

Expected: all documentation contracts pass.

- [ ] **Step 5: Commit operator documentation**

```bash
git add tests/test_documentation.py README.md docs/CLASSIFICATION_RULES.md
git commit -m "docs: publish pre-split filename contract"
```

### Task 4: Migrate and reclassify both real projects

**Files:**
- Modify ignored real data under: `validation_projects/项目1/`
- Modify ignored real data under: `validation_projects/项目2/`
- Modify: `docs/VALIDATION.md`

- [ ] **Step 1: Capture pre-migration evidence**

For both input directories, record first-level DXF count and a sorted mapping of each file stem to SHA-256. Confirm counts remain 72 and 171 before mutation.

- [ ] **Step 2: Run classification with overwrite**

Run:

```bash
uv run steel-dxf-classify validation_projects/项目1/项目1_dxf --overwrite
uv run steel-dxf-classify validation_projects/项目2/项目2_dxf --overwrite
```

Expected: project1 remains BH 67/BOX 2/PL 3; project2 remains BH 141/BOX 30; both have zero review/unreadable.

- [ ] **Step 3: Verify migration invariants**

Assert every first-level input and every routed output ends with `_拆板前.dxf`, no name contains `_拆板前_拆板前`, input/output counts match, and each routed file is byte-identical to its source. Compare content-hash multisets before/after to prove no DXF content changed.

- [ ] **Step 4: Update real validation evidence**

Add the migration scope, suffix invariants, unchanged hashes, updated elapsed times and unchanged type distributions to `docs/VALIDATION.md`.

- [ ] **Step 5: Commit validation documentation**

```bash
git add docs/VALIDATION.md
git commit -m "docs: validate real DXF filename migration"
```

### Task 5: Final build and release archives

**Files:**
- Rebuild: `dist/steel_dxf_classifier-1.0.0.tar.gz`
- Rebuild ignored project ZIPs in: `release_outputs/`
- Replace: `../steel_dxf_classifier_v1.0.0.zip`

- [ ] **Step 1: Run complete verification**

Run `uv sync --frozen`, full pytest, compileall, `uv build`, `git diff --check`, and assert clean `main`.

- [ ] **Step 2: Rebuild real-project ZIPs**

Recreate `项目1_分类整理结果.zip` and `项目2_分类整理结果.zip` from their migrated project directories; validate CRC and file counts and update `release_outputs/README.md` to mention `_拆板前.dxf`.

- [ ] **Step 3: Rebuild the complete project ZIP**

From the repository parent, use `zip -r -9 -y` to create a temporary complete archive preserving symlinks. Validate CRC, top-level directory, file/link count and presence of the preprocessing source/tests/docs; then atomically replace `steel_dxf_classifier_v1.0.0.zip`.

- [ ] **Step 4: Record final evidence**

Print SHA-256 for all three ZIPs, archive sizes, Git head/branch/status, and the final full-test count.
