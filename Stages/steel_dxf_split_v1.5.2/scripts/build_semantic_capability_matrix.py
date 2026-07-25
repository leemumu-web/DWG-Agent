#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def classify(report: dict) -> list[str]:
    diagnostics = report["diagnostics"]
    values = report["validation"]["values"]
    capabilities: list[str] = []
    if diagnostics.get("profile_variable_height"):
        capabilities.append("variable_height")
    mode = (diagnostics.get("flange_development") or {}).get("mode", "projection_view")
    if mode != "projection_view":
        capabilities.append(mode)
    if diagnostics.get("flange_component_count") == 2:
        capabilities.append("different_or_asymmetric_flanges")
    if values.get("circular_cut_count", 0) == 0:
        capabilities.append("holeless_web")
    if values.get("flange_circular_cut_count", 0) > 0:
        capabilities.append("flange_owned_cuts")
    if values.get("inner_contour_count", 0) > 0:
        capabilities.append("shaped_inner_openings")
    text = json.dumps(
        {
            "web": diagnostics.get("web_selection"),
            "flange": diagnostics.get("flange_selection"),
        },
        ensure_ascii=False,
    ).lower()
    if "boundary_completion" in text:
        capabilities.append("split_end_face_completion")
    if "hidden" in text:
        capabilities.append("hidden_geometry_evidence")
    if "regular" in text or "morph" in text:
        capabilities.append("bounded_micro_topology_repair")
    return sorted(set(capabilities))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    output = root / "output"
    samples = []
    capability_counts: Counter[str] = Counter()
    profile_counts: Counter[str] = Counter()
    maximum_error = 0.0
    for path in sorted(output.glob("*_自动拆板_报告.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("profile_family") != "BH":
            continue
        stem = path.name.split("_自动拆板")[0]
        values = report["validation"]["values"]
        capabilities = classify(report)
        capability_counts.update(capabilities)
        profile_counts[values["profile"]] += 1
        comparison = report.get("supervised_comparison") or {}
        error = float((comparison.get("values") or {}).get("max_plate_hausdorff_mm", 0.0) or 0.0)
        maximum_error = max(maximum_error, error)
        samples.append(
            {
                "stem": stem,
                "profile": values["profile"],
                "plate_geometry_count": len(values.get("plates", [])),
                "circular_cut_count": values.get("circular_cut_count", 0),
                "flange_circular_cut_count": values.get("flange_circular_cut_count", 0),
                "inner_contour_count": values.get("inner_contour_count", 0),
                "development_mode": (report["diagnostics"].get("flange_development") or {}).get("mode"),
                "capabilities": capabilities,
                "baseline_supervised_ok": comparison.get("ok"),
                "baseline_max_plate_hausdorff_mm": error,
            }
        )
    payload = {
        "version": "1.0.0",
        "scope": "20 supervised BH drawing pairs",
        "sample_count": len(samples),
        "profile_counts": dict(profile_counts),
        "capability_counts": dict(capability_counts),
        "maximum_baseline_supervised_error_mm": maximum_error,
        "semantic_dimensions": {
            "member_geometry": ["constant_height", "variable_height", "cranked_or_stepped"],
            "flange_identity": ["two_identical", "two_different", "asymmetric_feature_ownership"],
            "cut_features": ["holeless", "web_circles", "flange_circles", "shaped_openings"],
            "boundary_evidence": ["visible_edges", "hidden_arc_evidence", "split_end_faces", "micro_topology_noise"],
            "manufacturing_output": ["closed_plate_contours", "owned_cuts", "canonical_labels", "no_helper_lines"],
        },
        "samples": samples,
    }
    json_path = output / "BH语义能力矩阵_v1.0.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# BH 语义能力矩阵 v1.0",
        "",
        f"- 监督样本：{len(samples)} 对",
        f"- 基线最大人工轮廓差：{maximum_error:.6f} mm",
        "- 目标：验证能力维度的组合覆盖，而不是按构件号建立规则。",
        "",
        "## 能力覆盖",
        "",
    ]
    for key, value in sorted(capability_counts.items()):
        lines.append(f"- `{key}`：{value} 个样本")
    lines += [
        "",
        "## 样本矩阵",
        "",
        "| 构件 | 截面 | 孔 | 翼缘孔 | 内开口 | 展开模式 | 语义能力 | 人工差(mm) |",
        "|---|---|---:|---:|---:|---|---|---:|",
    ]
    for item in samples:
        lines.append(
            f"| {item['stem']} | {item['profile']} | {item['circular_cut_count']} | "
            f"{item['flange_circular_cut_count']} | {item['inner_contour_count']} | "
            f"{item['development_mode']} | {', '.join(item['capabilities']) or 'basic'} | "
            f"{item['baseline_max_plate_hausdorff_mm']:.6f} |"
        )
    (output / "BH语义能力矩阵_v1.0.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"samples": len(samples), "json": str(json_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
