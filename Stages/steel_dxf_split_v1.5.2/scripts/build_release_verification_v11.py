#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import json
import platform
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def python_312_grammar() -> dict[str, Any]:
    files = sorted((ROOT / "src").rglob("*.py"))
    failures: list[dict[str, str]] = []
    for path in files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 12))
        except SyntaxError as exc:
            failures.append({"file": str(path.relative_to(ROOT)), "error": str(exc)})
    return {
        "ok": not failures,
        "file_count": len(files),
        "failures": failures,
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def distribution_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(ROOT)),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else None,
        "sha256": sha256(path) if path.exists() else None,
    }


def main() -> int:
    atomic = load_json(OUTPUT / "v1.1_atomic_test_matrix.json")
    corpus = load_json(OUTPUT / "final_verification_v1.1.json")
    batch = load_json(OUTPUT / "batch_compilation_manifest.json") if (OUTPUT / "batch_compilation_manifest.json").exists() else None
    smoke = load_json(OUTPUT / "wheel_smoke_v1.1.json") if (OUTPUT / "wheel_smoke_v1.1.json").exists() else None
    wheel = ROOT / "dist" / f"steel_dxf_split-{VERSION}-py3-none-any.whl"
    sdist = ROOT / "dist" / f"steel_dxf_split-{VERSION}.tar.gz"
    grammar = python_312_grammar()

    report = {
        "version": VERSION,
        "schema": "BH-RELEASE-VERIFICATION-1.1",
        "scope": "BH manufacturing semantic compiler; BOX path retained for compatibility",
        "semantic_architecture": {
            "ontology_version": "BH-MFG-3.0",
            "report_schema": "BH-COMPILATION-REPORT-1.1",
            "contract_schema": "BH-CONTRACT-1.0",
            "risk_schema": "BH-RISK-REGISTER-1.1",
            "fingerprint_algorithm": "sha256-canonical-json-v1",
            "stages": [
                "Fact Frontend",
                "Geometry IR",
                "Annotation and Metadata Semantics",
                "Diverse Complete-Hypothesis Frontier",
                "Manufacturing Lowering",
                "Hard and Soft Constraint Evaluation",
                "Global Selection and Automation Quality Gate",
                "Manufacturing Contract Freeze",
                "Deterministic DXF Backend",
                "Static and Supervised Verification",
            ],
        },
        "runtime_verification": {
            "atomic_test_matrix": {
                key: atomic[key]
                for key in ("collected", "executed", "passed", "failed", "timeouts", "complete", "all_passed")
            },
            "corpus": corpus,
            "batch_manifest": batch,
            "wheel_smoke": smoke,
        },
        "compatibility": {
            "python_312_grammar": grammar,
            "runtime_python": platform.python_version(),
            "platform": platform.platform(),
        },
        "distribution": {
            "wheel": distribution_record(wheel),
            "sdist": distribution_record(sdist),
        },
    }
    checks = {
        "version_matches": VERSION == "1.1.0",
        "atomic_tests_complete": atomic.get("complete") is True and atomic.get("all_passed") is True,
        "atomic_test_count": atomic.get("passed") == 115,
        "corpus_complete": corpus.get("pair_count") == 20 and corpus.get("all_passed") is True,
        "all_contracts_ready": corpus.get("all_contracts_production_ready") is True,
        "all_auto_accept": corpus.get("all_auto_accept") is True,
        "all_outputs_clean": corpus.get("all_audit_clean") is True and corpus.get("all_helper_lines_removed") is True,
        "all_critical_and_high_risks_guarded": corpus.get("all_critical_and_high_risks_guarded") is True,
        "all_risk_registers_failure_free": corpus.get("all_risk_registers_failure_free") is True,
        "fingerprints_unique": corpus.get("unique_manufacturing_fingerprint_count") == 20,
        "python_312_compatible": grammar["ok"],
        "wheel_exists": wheel.exists(),
        "sdist_exists": sdist.exists(),
        "wheel_smoke_passed": bool(smoke and smoke.get("passed") is True),
    }
    if batch is not None:
        checks["batch_all_passed"] = batch.get("all_passed") is True and batch.get("passed_count") == 20
    report["checks"] = checks
    report["release_ready"] = all(checks.values())
    (ROOT / "RELEASE_VERIFICATION.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"release_ready": report["release_ready"], "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if report["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
