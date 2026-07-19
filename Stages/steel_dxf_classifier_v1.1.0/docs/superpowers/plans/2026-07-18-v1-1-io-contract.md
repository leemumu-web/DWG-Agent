# 1.1 Input Output Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release 1.1.0 with stable human/JSON CLI streams, unambiguous exit codes, synchronized report/version metadata, and formal operator documentation.

**Architecture:** Keep classification behavior and file routing unchanged. Extend `cli.py` with a strict argument parser and a JSON serializer that projects `BatchSummary`; centralize 1.1.0 in the package, use it for report/CLI schemas, and document the end-to-end filesystem and process streams in a dedicated contract.

**Tech Stack:** Python 3.13, argparse, json, pytest, ezdxf, uv, Info-ZIP.

---

### Task 1: Define stable CLI output and exit semantics with tests

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/steel_dxf_classifier/cli.py`

- [ ] **Step 1: Write failing CLI contract tests**

Add tests that invoke `main(["--json", str(source)])` for one BOX file and assert stdout parses as exactly one object with `schema="STEEL-DXF-CLI-1.1"`, `status="completed"`, `exit_code=0`, and `summary.type_counts={"BOX": 1}`. Add a review case asserting `status="completed_with_review"` and return value 2. Add a malformed input case asserting return 64, empty stdout and stderr beginning `错误:`. Add a version case asserting `steel-dxf-classifier 1.1.0`.

- [ ] **Step 2: Verify the tests fail**

Run: `uv run pytest -q tests/test_cli.py`

Expected: `--json` and `--version` are unsupported and invalid-input exit code is not 64.

- [ ] **Step 3: Implement the minimal CLI contract**

Create `CLI_SCHEMA = "STEEL-DXF-CLI-1.1"`, `USAGE_ERROR_EXIT = 64`, `_exit_code(summary)` and `_json_payload(summary, code)`. Add `--json` and `--version`; use an `ArgumentParser` subclass whose `error()` writes `错误:` to stderr and exits 64. On success, emit either the existing Chinese summary or exactly `json.dumps(payload, ensure_ascii=False, sort_keys=True)` plus newline. On exceptions, keep stdout empty and write one `错误:` line to stderr.

- [ ] **Step 4: Verify CLI tests pass**

Run: `uv run pytest -q tests/test_cli.py`

Expected: all CLI tests pass.

- [ ] **Step 5: Commit CLI contract**

```bash
git add tests/test_cli.py src/steel_dxf_classifier/cli.py
git commit -m "feat: add stable JSON CLI output"
```

### Task 2: Synchronize version and report metadata

**Files:**
- Modify: `VERSION`
- Modify: `pyproject.toml`
- Modify: `src/steel_dxf_classifier/__init__.py`
- Modify: `src/steel_dxf_classifier/batch.py`
- Modify: `tests/test_batch.py`

- [ ] **Step 1: Write failing 1.1 metadata assertions**

Change the report-schema assertion to `STEEL-DXF-CLASSIFICATION-1.1`. Add assertions that `VERSION`, `steel_dxf_classifier.__version__`, and `pyproject.toml` contain 1.1.0.

- [ ] **Step 2: Verify metadata tests fail**

Run: `uv run pytest -q tests/test_batch.py tests/test_model.py`

Expected: failure shows the 1.0 schema/version values.

- [ ] **Step 3: Implement synchronized 1.1 metadata**

Set all release version locations to `1.1.0` and report schema to `STEEL-DXF-CLASSIFICATION-1.1`. Do not change report fields or classification decisions.

- [ ] **Step 4: Verify metadata tests pass**

Run: `uv run pytest -q tests/test_batch.py tests/test_model.py`

Expected: all selected tests pass.

- [ ] **Step 5: Commit version and report update**

```bash
git add VERSION pyproject.toml src/steel_dxf_classifier/__init__.py src/steel_dxf_classifier/batch.py tests/test_batch.py tests/test_model.py
git commit -m "release: prepare 1.1.0 metadata"
```

### Task 3: Publish the formal I/O contract

**Files:**
- Create: `docs/IO_CONTRACT.md`
- Modify: `README.md`
- Modify: `docs/CLASSIFICATION_RULES.md`
- Modify: `docs/VALIDATION.md`
- Modify: `tests/test_documentation.py`

- [ ] **Step 1: Write failing documentation contract assertions**

Require `docs/IO_CONTRACT.md`, `--json`, `STEEL-DXF-CLI-1.1`, `64`, `stdout`, `stderr`, `completed_with_review`, `STEEL-DXF-CLASSIFICATION-1.1`, and `1.1.0` across operator docs.

- [ ] **Step 2: Verify documentation tests fail**

Run: `uv run pytest -q tests/test_documentation.py`

Expected: missing I/O document and 1.1 terms cause failure.

- [ ] **Step 3: Write the operator contract and synchronize references**

Document input validation, preprocessing timing, output directories/reports, default stdout, JSON stdout envelope, stderr rules, exit codes, overwrite/rollback behavior and examples. Update README as the quick start and link the detailed contract. Update rule/validation docs to use 1.1 report schema and release version.

- [ ] **Step 4: Verify documentation tests pass**

Run: `uv run pytest -q tests/test_documentation.py`

Expected: all documentation tests pass.

- [ ] **Step 5: Commit documentation release material**

```bash
git add docs/IO_CONTRACT.md README.md docs/CLASSIFICATION_RULES.md docs/VALIDATION.md tests/test_documentation.py
git commit -m "docs: publish 1.1 input output contract"
```

### Task 4: Validate 1.1 on real projects and publish artifacts

**Files:**
- Modify ignored data under: `validation_projects/项目1/`, `validation_projects/项目2/`
- Rebuild ignored ZIPs in: `release_outputs/`
- Replace: `../steel_dxf_classifier_v1.0.0.zip`

- [ ] **Step 1: Run both real inputs through JSON mode**

Run `uv run steel-dxf-classify --json validation_projects/项目1/项目1_dxf --overwrite` and the project2 equivalent. Parse each stdout as one JSON object and compare its `summary` with the corresponding report JSON. Require project1 BH 67/BOX 2/PL 3 and project2 BH 141/BOX 30, both with exit 0.

- [ ] **Step 2: Run complete release verification**

Run `uv sync --frozen`, full pytest, compileall, `uv build`, `git diff --check`, and clean-main checks.

- [ ] **Step 3: Rebuild archives transactionally**

Recreate both project result ZIPs from their project directories, CRC-test and count entries before replacement. Rebuild the complete project ZIP using `zip -r -9 -y`, verify CRC, top-level path and file/link count, then atomically replace the old archive.

- [ ] **Step 4: Record final evidence**

Print archive SHA-256 values, sizes, Git head, version, full test count and the two JSON-mode real-project summaries.
