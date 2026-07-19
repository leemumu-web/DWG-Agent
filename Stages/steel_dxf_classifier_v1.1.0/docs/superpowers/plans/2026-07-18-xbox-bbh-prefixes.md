# XBOX and BBH Registered Prefixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register XBOX and BBH as independent part types, document them, and rebuild verified release artifacts.

**Architecture:** Extend only the centralized `REGISTERED_TYPES` taxonomy. The existing full-letter-prefix parser and batch router will carry the exact type through to output directory names; no aliases or special-case matching are added.

**Tech Stack:** Python 3.13, pytest, ezdxf, uv, Info-ZIP.

---

### Task 1: Registered profile parsing

**Files:**
- Modify: `tests/test_profile.py`
- Modify: `src/steel_dxf_classifier/profile.py`

- [ ] **Step 1: Write failing parsing tests**

Add `("XBOX300*300*10*10", "XBOX")` and `("BBH600*200*12*22", "BBH")` to `test_parse_profile_preserves_concrete_prefix`, and add both names to `test_registered_taxonomy_keeps_specific_engineering_prefixes`.

- [ ] **Step 2: Verify the tests fail for catalog registration**

Run: `uv run pytest -q tests/test_profile.py`

Expected: failures show `catalog_status == "unregistered"` and the registered taxonomy assertion is missing XBOX/BBH.

- [ ] **Step 3: Add the minimal registry entries**

Change the welded/box group in `REGISTERED_TYPES` to:

```python
"BH", "BBH", "BOX", "XBOX", "BT",
```

- [ ] **Step 4: Verify profile tests pass**

Run: `uv run pytest -q tests/test_profile.py`

Expected: all profile tests pass, with XBOX and BBH reported as registered concrete types.

- [ ] **Step 5: Commit parser support**

```bash
git add tests/test_profile.py src/steel_dxf_classifier/profile.py
git commit -m "feat: register XBOX and BBH part types"
```

### Task 2: Routing and operator documentation

**Files:**
- Modify: `tests/test_batch.py`
- Modify: `tests/test_documentation.py`
- Modify: `README.md`
- Modify: `docs/CLASSIFICATION_RULES.md`

- [ ] **Step 1: Write failing routing and documentation tests**

Add a batch test that creates `xbox.dxf` with `XBOX300*300*10*10` and `bbh.dxf` with `BBH600*200*12*22`, then asserts both exact output directories exist and `type_counts == {"BBH": 1, "XBOX": 1}`. Add `XBOX` and `BBH` to the required documentation terms.

- [ ] **Step 2: Verify the documentation contract fails**

Run: `uv run pytest -q tests/test_batch.py tests/test_documentation.py`

Expected: routing already uses exact prefixes, while documentation fails because XBOX/BBH are not yet listed.

- [ ] **Step 3: Synchronize operator documentation**

List XBOX and BBH in the README examples and built-in welded/box group. Update the corresponding classification-rules table row to `BH, BBH, BOX, XBOX, BT`.

- [ ] **Step 4: Verify routing and documentation tests pass**

Run: `uv run pytest -q tests/test_batch.py tests/test_documentation.py`

Expected: all selected tests pass.

- [ ] **Step 5: Commit routing regression and docs**

```bash
git add tests/test_batch.py tests/test_documentation.py README.md docs/CLASSIFICATION_RULES.md
git commit -m "docs: publish XBOX and BBH routing contract"
```

### Task 3: Release verification and complete project ZIP

**Files:**
- Rebuild: `dist/steel_dxf_classifier-1.0.0.tar.gz`
- Rebuild: `dist/steel_dxf_classifier-1.0.0-py3-none-any.whl`
- Replace: `../steel_dxf_classifier_v1.0.0.zip`

- [ ] **Step 1: Run the complete verification suite**

Run:

```bash
uv sync --frozen
uv run pytest -q
uv run python -m compileall -q src tests
uv build
git diff --check
```

Expected: all tests pass, compilation is silent, both distributions build, and diff check exits 0.

- [ ] **Step 2: Confirm repository state**

Run: `git status --short && git branch --show-current`

Expected: no status output and branch `main`.

- [ ] **Step 3: Rebuild the complete project archive safely**

From `/home/Creeken/Paper/CAD_research`, create `steel_dxf_classifier_v1.0.0.rebuild.zip` with `zip -r -9 -y`, preserving symbol links; validate CRC, top directory and file/link count, then atomically replace `steel_dxf_classifier_v1.0.0.zip`.

- [ ] **Step 4: Record final archive evidence**

Run:

```bash
unzip -t steel_dxf_classifier_v1.0.0.zip
sha256sum steel_dxf_classifier_v1.0.0.zip
```

Expected: no compressed-data errors and a stable SHA-256 digest is printed.
