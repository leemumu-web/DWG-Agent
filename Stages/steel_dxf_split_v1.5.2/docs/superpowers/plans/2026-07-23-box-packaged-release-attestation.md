# BOX Packaged Release Attestation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the validated BOX release attestation a wheel-internal default while preserving explicit-path override and fail-closed verification.

**Architecture:** `box.release` owns resource loading and validation. `box.compiler` always requests one verified attestation; a supplied path overrides the packaged resource, while no path selects `release_evidence/box_release_attestation.json`. The attestation is regenerated only after production code stabilizes because it binds the implementation fingerprint.

**Tech Stack:** Python 3.12, `importlib.resources`, pytest, uv/setuptools wheel packaging, existing BOX release-gate tooling.

## Global Constraints

- Do not change BH/BOX manufacturing geometry or routing semantics.
- Preserve explicit `--box-release-attestation` as an override.
- Missing, invalid, or stale packaged evidence must fail closed.
- Generate attestation only from the read-only 20-pair BOX authority corpus.
- Do not stage the existing untracked `release/` directory.

---

### Task 1: Package-aware attestation loader

**Files:**
- Modify: `tests/box_v1/test_release.py`
- Modify: `tools/verify_bh_v152_source.py`
- Modify: `src/steel_dxf_split/box/release.py`

**Interfaces:**
- Consumes: `load_verified_box_release_attestation(path)` existing explicit-path loader.
- Produces: `load_verified_box_release_attestation(path: Path | None = None)` with packaged-resource fallback.

- [x] Add tests that request the packaged default, require a real packaged JSON resource, and retain explicit-path precedence.
- [x] Run the tests and confirm they fail because the optional-path behavior/resource does not exist.
- [x] Implement resource loading in `box.release` without weakening digest, count, core, or implementation checks.
- [x] Run the focused release tests and confirm the loader tests pass once a current attestation exists.

### Task 2: Compiler default and fail-closed behavior

**Files:**
- Modify: `tests/test_box_gate_integration_v1.py`
- Modify: `tests/test_box_single_core_route.py`
- Modify: `tests/box_v1/test_delivery.py`
- Modify: `src/steel_dxf_split/box/compiler.py`

**Interfaces:**
- Consumes: optional-path `load_verified_box_release_attestation` from Task 1.
- Produces: BOX compilation that uses packaged evidence when no explicit path is supplied.

- [x] Replace the former “no external path routes to review” expectation with packaged-default production behavior.
- [x] Add a focused assertion that an explicit attestation path is still propagated unchanged.
- [x] Run focused tests and confirm failure occurs at the former external-path requirement.
- [x] Remove the external-path precondition and always load a verified default-or-explicit attestation.
- [x] Run focused compiler and unified-route tests.

### Task 3: Regenerate current packaged certification

**Files:**
- Create: `src/steel_dxf_split/release_evidence/box_release_attestation.json`

**Interfaces:**
- Consumes: `scripts/verify_box_v1_fusion.py` and the 20 read-only before/after DXF pairs.
- Produces: implementation-bound `BOX-RELEASE-ATTESTATION-2.0` package resource.

- [x] Run the 20-pair release gate into a temporary output directory and emit the packaged attestation.
- [x] Verify source and reference corpus hashes remain unchanged.
- [x] Load the generated resource through the default loader and confirm its implementation fingerprint matches current code.

### Task 4: Build and installed-wheel acceptance

**Files:**
- Modify: `README.md`
- Modify: `CONTEXT.md`
- Modify: `src/steel_dxf_split/cli.py`
- Modify: `src/steel_dxf_split/batch_cli.py`

**Interfaces:**
- Consumes: final source tree and packaged attestation.
- Produces: one self-contained wheel requiring no companion BOX certification file.

- [x] Update documentation and CLI help: external path is optional override, not a required companion.
- [x] Run focused tests, complete pytest, Ruff, and `git diff --check`.
- [x] Build a wheel and inspect it for `release_evidence/box_release_attestation.json`.
- [x] Install the wheel in an isolated environment and compile one BOX sample with `--require-auto-accept` but without `--box-release-attestation`.
- [x] Verify the installed result DXF can be reread and audited without errors.
- [x] Review the staged diff, stage only intended files, and commit the completed change.
