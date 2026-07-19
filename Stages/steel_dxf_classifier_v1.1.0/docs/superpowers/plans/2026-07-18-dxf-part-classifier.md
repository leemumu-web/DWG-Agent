# DXF Part Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent Linux CLI that classifies first-level DXF part drawings into sibling project/type directories from title-block profile evidence.

**Architecture:** A loss-minimizing DXF reader emits positioned text facts; a title-block pass pairs authoritative labels with profile values; a pure profile parser derives concrete prefixes; a batch transaction copies files and emits JSON/CSV evidence. Missing or conflicting evidence fails closed into review rather than guessing from filenames or material rows.

**Tech Stack:** Python 3.12+, ezdxf 1.4.x, standard-library dataclasses/argparse/csv/json/shutil, pytest, uv/hatchling.

---

### Task 1: Package contract and domain model

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `VERSION`
- Create: `src/steel_dxf_classifier/__init__.py`
- Create: `src/steel_dxf_classifier/model.py`
- Create: `tests/test_model.py`

- [ ] **Step 1: Write the failing model test**

```python
from steel_dxf_classifier.model import Disposition, TextFact


def test_text_fact_serializes_source_evidence() -> None:
    fact = TextFact("BH300*200*6*8", "BH300*200*6*8", 10.0, 20.0, 3.0, "TEXT", "Other", "2A", ("FRAME",))
    assert fact.to_dict()["block_path"] == ["FRAME"]
    assert Disposition.CLASSIFIED.value == "classified"
```

- [ ] **Step 2: Run `uv run pytest tests/test_model.py -q` and verify import failure**

- [ ] **Step 3: Add package metadata and immutable dataclasses**

`model.py` defines `Disposition`, `TextFact`, `ProfileParse`, `TitleCandidate`, and `ClassificationResult`; every record exposes JSON-safe `to_dict()` data and retains diagnostics/evidence.

- [ ] **Step 4: Run the model test and `python -m compileall -q src tests`**

- [ ] **Step 5: Commit with `git commit -m "build: scaffold independent DXF classifier"`**

### Task 2: Text normalization and profile taxonomy

**Files:**
- Create: `src/steel_dxf_classifier/text.py`
- Create: `src/steel_dxf_classifier/profile.py`
- Create: `tests/test_profile.py`

- [ ] **Step 1: Write failing table-driven tests**

```python
import pytest
from steel_dxf_classifier.profile import parse_profile


@pytest.mark.parametrize(("raw", "kind"), [
    ("BH1500*500*30*30", "BH"),
    (" box600×600×30×30 ", "BOX"),
    ("ＰＬ 20＊300", "PL"),
    ("HN400*200*8*13", "HN"),
    ("RHS200*100*8", "RHS"),
    ("L90*8", "L"),
    ("TT25", "TT"),
])
def test_parse_profile_preserves_concrete_prefix(raw: str, kind: str) -> None:
    parsed = parse_profile(raw)
    assert parsed is not None and parsed.part_type == kind


@pytest.mark.parametrize("raw", ["400*200*8*13", "Q355B", "1:10", "../BH300"])
def test_parse_profile_rejects_non_profile_or_unsafe_values(raw: str) -> None:
    assert parse_profile(raw) is None
```

- [ ] **Step 2: Run profile tests and verify module import failure**

- [ ] **Step 3: Implement NFKC normalization, multiplication-symbol normalization, the registered-prefix set, safe dynamic ASCII prefixes, and numeric dimension-body validation**

- [ ] **Step 4: Run `uv run pytest tests/test_profile.py -q` and the full suite**

- [ ] **Step 5: Commit with `git commit -m "feat: parse comprehensive profile prefixes"`**

### Task 3: Loss-aware DXF text reader

**Files:**
- Create: `src/steel_dxf_classifier/reader.py`
- Create: `tests/test_reader.py`

- [ ] **Step 1: Write failing synthetic-DXF tests**

```python
import ezdxf
from steel_dxf_classifier.reader import read_text_facts


def test_reader_expands_insert_text_and_attributes(tmp_path) -> None:
    doc = ezdxf.new("R2010")
    block = doc.blocks.new("TITLE")
    block.add_text("截面", dxfattribs={"insert": (0, 10), "height": 2})
    block.add_attdef("PROFILE", insert=(0, 0), text="")
    insert = doc.modelspace().add_blockref("TITLE", (100, 200))
    insert.add_auto_attribs({"PROFILE": "BH300*200*6*8"})
    path = tmp_path / "part.dxf"
    doc.saveas(path)
    facts, metadata = read_text_facts(path)
    assert {fact.normalized for fact in facts} >= {"截面", "BH300*200*6*8"}
    assert metadata["preview_encoding"] in {"utf-8", "cp1252"}
```

- [ ] **Step 2: Run reader tests and verify module import failure**

- [ ] **Step 3: Implement declared-codepage rereading plus recursive INSERT, TEXT, MTEXT and ATTRIB traversal with transformed coordinates and block paths**

- [ ] **Step 4: Add a corrupted-file test expecting `DXFReadError`, then run reader tests and full suite**

- [ ] **Step 5: Commit with `git commit -m "feat: read positioned DXF title text"`**

### Task 4: Evidence-driven title-block classification

**Files:**
- Create: `src/steel_dxf_classifier/title_block.py`
- Create: `src/steel_dxf_classifier/classify.py`
- Create: `tests/test_classify.py`

- [ ] **Step 1: Write failing classification tests**

```python
from steel_dxf_classifier.classify import classify_facts
from steel_dxf_classifier.model import Disposition, TextFact


def fact(text: str, x: float, y: float) -> TextFact:
    return TextFact(text, text, x, y, 3.0, "TEXT", "Other", None, ())


def test_unique_upper_right_section_value_is_classified() -> None:
    result = classify_facts("a.dxf", [fact("截面", 80, 95), fact("BH300*200*6*8", 78, 85), fact("Q355B", 90, 85)])
    assert result.disposition is Disposition.CLASSIFIED
    assert result.part_type == "BH"


def test_material_table_with_multiple_profile_rows_requires_review() -> None:
    rows = [fact("规格", 80, 95), fact("PL10*100", 80, 90), fact("PL12*200", 80, 85)]
    result = classify_facts("assembly.dxf", rows)
    assert result.disposition is Disposition.REVIEW_REQUIRED
    assert "TITLE_VALUE_CONFLICT" in result.diagnostics
```

- [ ] **Step 2: Run classification tests and verify module import failure**

- [ ] **Step 3: Implement relative page-region filtering, label-relative candidate bands, exact-one-value proof, candidate evidence, and fail-closed decisions**

- [ ] **Step 4: Add tests for lower-left labels, same-row values, unregistered prefixes, missing labels and conflicting types; run the full suite**

- [ ] **Step 5: Commit with `git commit -m "feat: classify title-block profile evidence"`**

### Task 5: First-level batch transaction and reports

**Files:**
- Create: `src/steel_dxf_classifier/batch.py`
- Create: `tests/test_batch.py`

- [ ] **Step 1: Write failing end-to-end batch test**

```python
def test_batch_copies_first_level_files_and_routes_failures(tmp_path, make_part_dxf) -> None:
    source = tmp_path / "项目2_dxf"
    source.mkdir()
    make_part_dxf(source / "bh.dxf", "BH300*200*6*8")
    (source / "broken.dxf").write_text("not dxf", encoding="utf-8")
    nested = source / "nested"
    nested.mkdir()
    make_part_dxf(nested / "ignored.dxf", "BOX300*300*10*10")
    summary = classify_directory(source)
    assert (tmp_path / "项目2_BH_dxf" / "bh.dxf").is_file()
    assert (tmp_path / "项目2_无法读取_dxf" / "broken.dxf").is_file()
    assert not (tmp_path / "项目2_BOX_dxf").exists()
    assert summary.input_count == 2
```

- [ ] **Step 2: Run batch test and verify module import failure**

- [ ] **Step 3: Implement strict `<project>_dxf` parsing, sorted first-level enumeration, staging copies, overwrite protection, JSON/CSV manifests and rollback-safe promotion**

- [ ] **Step 4: Add tests for review routing, Chinese filenames, existing-output refusal, `--overwrite`, no source mutation and manifest consistency; run full suite**

- [ ] **Step 5: Commit with `git commit -m "feat: classify DXF directories transactionally"`**

### Task 6: CLI, operator documentation and real Tekla validation

**Files:**
- Create: `src/steel_dxf_classifier/cli.py`
- Create: `README.md`
- Create: `docs/CLASSIFICATION_RULES.md`
- Create: `tests/test_cli.py`
- Create: `tests/test_documentation.py`

- [ ] **Step 1: Write failing CLI tests for success, review exit code 2, invalid directory names and `--overwrite`**

- [ ] **Step 2: Run CLI tests and verify the entry point is missing**

- [ ] **Step 3: Implement argparse CLI and document exact input/output layout, evidence precedence, taxonomy, diagnostics and rerun safety**

- [ ] **Step 4: Run all tests and `uv run python -m compileall -q src tests`**

- [ ] **Step 5: Create a temporary `项目验证_dxf` containing copies of representative Tekla BH inputs, run `uv run steel-dxf-classify <path>`, verify BH routing and inspect JSON evidence; remove only the temporary validation tree**

- [ ] **Step 6: Run `git diff --check`, verify `git status --short`, and commit with `git commit -m "docs: publish DXF classifier operator workflow"`**
