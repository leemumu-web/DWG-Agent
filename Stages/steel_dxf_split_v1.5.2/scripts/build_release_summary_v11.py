from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import ezdxf

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"


def main() -> None:
    reports = sorted(OUTPUT.glob("*_自动拆板_报告.json"))
    summary: dict[str, object] = {}
    fingerprints: dict[str, str] = {}
    all_ok = True
    max_hausdorff = 0.0
    max_hole_center = 0.0
    min_confidence = 1.0
    for report_path in reports:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        stem = report_path.name.split("_自动拆板_报告.json")[0]
        clean = Path(report["outputs"]["clean"])
        doc = ezdxf.readfile(clean)
        audit_errors = len(doc.audit().errors)
        counts = Counter(entity.dxftype() for entity in doc.modelspace())
        helper_count = sum(counts.get(kind, 0) for kind in ("LINE", "XLINE", "RAY"))
        comparison = report.get("supervised_comparison") or {}
        values = comparison.get("values", {})
        contract = report.get("manufacturing_contract") or {}
        assessment = report.get("automation_assessment") or {}
        fp = report.get("semantic_fingerprints") or {}
        risk = report.get("risk_analysis") or {}
        item_ok = all(
            (
                report.get("version") == "1.1.0",
                comparison.get("ok") is True,
                audit_errors == 0,
                helper_count == 0,
                contract.get("hard_pass") is True,
                contract.get("safety_pass") is True,
                contract.get("production_ready") is True,
                assessment.get("disposition") == "auto_accept",
                len(fp.get("manufacturing_ir", "")) == 64,
                risk.get("all_critical_and_high_guarded") is True,
                risk.get("failed_count") == 0,
            )
        )
        all_ok = all_ok and item_ok
        max_hausdorff = max(max_hausdorff, float(values.get("max_plate_hausdorff_mm", 0.0) or 0.0))
        max_hole_center = max(
            max_hole_center,
            float(values.get("max_circular_cut_center_difference_mm", 0.0) or 0.0),
        )
        min_confidence = min(min_confidence, float(assessment.get("confidence", 0.0) or 0.0))
        fingerprints[stem] = fp.get("manufacturing_ir", "")
        summary[stem] = {
            "ok": item_ok,
            "profile": report.get("validation", {}).get("values", {}).get("profile"),
            "plate_count": len(report.get("validation", {}).get("values", {}).get("labels", [])),
            "circular_cut_count": report.get("validation", {}).get("values", {}).get("circular_cut_count"),
            "inner_contour_count": report.get("validation", {}).get("values", {}).get("inner_contour_count"),
            "supervised_max_plate_hausdorff_mm": values.get("max_plate_hausdorff_mm"),
            "supervised_max_hole_center_difference_mm": values.get("max_circular_cut_center_difference_mm"),
            "automation_disposition": assessment.get("disposition"),
            "confidence": assessment.get("confidence"),
            "risk_flags": assessment.get("risk_flags", []),
            "contract_production_ready": contract.get("production_ready"),
            "risk_all_critical_and_high_guarded": risk.get("all_critical_and_high_guarded"),
            "risk_failed_count": risk.get("failed_count"),
            "risk_review_count": risk.get("review_count"),
            "risk_status_counts": risk.get("status_counts", {}),
            "applicable_risks": [item.get("risk_id") for item in risk.get("observations", []) if item.get("applicable")],
            "evidence_channels": contract.get("evidence_channels", []),
            "source_fact_fingerprint": fp.get("source_fact_ir"),
            "selected_hypothesis_fingerprint": fp.get("selected_hypothesis"),
            "manufacturing_fingerprint": fp.get("manufacturing_ir"),
            "dxf_audit_errors": audit_errors,
            "line_xline_ray_count": helper_count,
            "entity_counts": dict(counts),
            "clean_output": str(clean),
            "report": str(report_path),
        }

    corpus = {
        "version": "1.1.0",
        "report_schema": "BH-CORPUS-VERIFICATION-1.1",
        "pair_count": len(summary),
        "all_passed": all_ok and len(summary) == 20,
        "all_auto_accept": all(item["automation_disposition"] == "auto_accept" for item in summary.values()),
        "all_contracts_production_ready": all(item["contract_production_ready"] for item in summary.values()),
        "all_critical_and_high_risks_guarded": all(item["risk_all_critical_and_high_guarded"] for item in summary.values()),
        "all_risk_registers_failure_free": all(item["risk_failed_count"] == 0 for item in summary.values()),
        "all_audit_clean": all(item["dxf_audit_errors"] == 0 for item in summary.values()),
        "all_helper_lines_removed": all(item["line_xline_ray_count"] == 0 for item in summary.values()),
        "minimum_confidence": min_confidence,
        "maximum_supervised_hausdorff_mm": max_hausdorff,
        "maximum_hole_center_difference_mm": max_hole_center,
        "unique_manufacturing_fingerprint_count": len(set(fingerprints.values())),
        "results": summary,
    }
    (OUTPUT / "二十组BH语义编译回归汇总_v1.1.json").write_text(
        json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    final = {
        key: corpus[key]
        for key in (
            "version",
            "report_schema",
            "pair_count",
            "all_passed",
            "all_auto_accept",
            "all_contracts_production_ready",
            "all_critical_and_high_risks_guarded",
            "all_risk_registers_failure_free",
            "all_audit_clean",
            "all_helper_lines_removed",
            "minimum_confidence",
            "maximum_supervised_hausdorff_mm",
            "maximum_hole_center_difference_mm",
            "unique_manufacturing_fingerprint_count",
        )
    }
    (OUTPUT / "final_verification_v1.1.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(final, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
