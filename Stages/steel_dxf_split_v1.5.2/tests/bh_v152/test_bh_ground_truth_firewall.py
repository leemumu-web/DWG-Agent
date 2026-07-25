from __future__ import annotations

import inspect
from pathlib import Path

from steel_dxf_split.bh_compiler import BHCompiler, compile_bh_document
from steel_dxf_split.bh_solver import solve_component_hypotheses


ROOT = Path(__file__).resolve().parents[2]


def test_compiler_entrypoints_have_no_ground_truth_or_expected_result_channel() -> None:
    public_parameters = set(inspect.signature(compile_bh_document).parameters)
    method_parameters = set(inspect.signature(BHCompiler.compile).parameters)
    solver_parameters = set(inspect.signature(solve_component_hypotheses).parameters)
    prohibited = {
        "manual",
        "manual_path",
        "manual_reference",
        "ground_truth",
        "expected",
        "expected_plate_count",
        "sample_stem",
    }

    assert public_parameters == {
        "doc",
        "source_contract",
        "source_path",
        "observer",
    }
    assert method_parameters == {
        "self",
        "doc",
        "source_contract",
        "source_path",
        "observer",
    }
    assert not prohibited.intersection(solver_parameters)


def test_compiler_and_solver_modules_do_not_import_post_hoc_comparison() -> None:
    compilation_modules = (
        "bh_compiler.py",
        "bh_passes.py",
        "bh_solver.py",
        "bh_constraints.py",
        "bh_hypothesis.py",
        "bh_extractor.py",
    )
    combined = "\n".join(
        (ROOT / "src" / "steel_dxf_split" / name).read_text(encoding="utf-8")
        for name in compilation_modules
    )

    assert "compare_bh_to_manual" not in combined
    assert "manual_reference_path" not in combined
    assert "拆板后" not in combined


def test_production_package_contains_no_corpus_stem_special_cases() -> None:
    stems = {
        path.name.removesuffix("_拆板前.dxf")
        for path in (ROOT / "samples" / "bh_pairs").glob("*_拆板前.dxf")
    }
    production_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "steel_dxf_split").glob("bh_*.py")
    )

    assert stems
    assert not {stem for stem in stems if stem in production_source}


def test_release_verifier_defers_manual_hash_and_geometry_until_post_hoc() -> None:
    source = (ROOT / "scripts" / "bh" / "build_bh_release_verification.py").read_text(
        encoding="utf-8"
    )

    freeze = source.index("integrated_writer = _verify_integrated_writer")
    manual_hash = source.index("validate_corpus_manual_file", freeze)
    comparison = source.index("compare_bh_to_manual", manual_hash)
    assert freeze < manual_hash < comparison
