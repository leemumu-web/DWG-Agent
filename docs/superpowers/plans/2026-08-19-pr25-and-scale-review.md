# PR25 and DXF Scale Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge PR25, keep the source-backed BH flange rules only where their evidence is valid, and deliver 85 target drawings with physical-millimetre production geometry.

**Architecture:** DXF `$INSUNITS=4` and source model/block geometry remain the production length authority. Title-block `1:n` and `$LTSCALE` stay descriptive/display metadata; they must not scale CAM-facing output coordinates. BH/BOX split and frontend/database/package contracts remain unchanged.

**Tech Stack:** Python 3.12, ezdxf 1.4.4, Shapely, pytest, ruff, Git, the existing combined-sheet generator.

---

## Task 1: Preserve the merged PR and remove invalid output scaling

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/bh_semantics.py`
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pipeline.py`
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/weld_allowance.py`
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/box/weld_allowance.py`
- Delete: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/drawing_scale.py`
- Delete: `backend/tests/dxf_splitting/test_drawing_scale.py`

- [ ] Restore the merged PR files as the only algorithm changes and remove the previous title-scale coordinate transform.
- [ ] Verify clean and allowance DXFs remain physical millimetres and retain the existing fixed allowance contract.

## Task 2: Review PR25 behavior against real target drawings

**Files:**
- Test: `backend/tests/dxf_splitting/test_bh_main_flange_course_authority.py`
- Test: `backend/tests/dxf_splitting/test_bh_flange_projection_regressions.py`

- [ ] Run focused PR tests and real BH compilation samples.
- [ ] Fix only reproducible rule or evidence defects; leave valid behavior unchanged.

## Task 3: Verify the 85-file production path

**Files:**
- Use: `准备优化的目标图纸/`
- Use: `准备优化的目标图纸/generate_combined_two_column_sheet.py`

- [ ] Run all 85 BH/BOX inputs through the actual classified CLI path.
- [ ] Confirm the expected 82 auto-accepted and 3 known BOX metadata failures.
- [ ] Validate output DXF units and physical dimensions, then generate the combined two-column sheet.
