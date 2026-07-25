from __future__ import annotations

import json
from pathlib import Path

from steel_dxf_split.bh_compare import compare_bh_to_manual
from steel_dxf_split.bh_extractor import extract_bh_assembly
from steel_dxf_split.extractor import load_document

ROOT = Path(__file__).resolve().parents[1]
PAIR_DIR = ROOT / "samples" / "bh_pairs"
OLD_OUTPUT = ROOT.parent / "steel_dxf_split_v0.7.0" / "output"
OUTPUT = ROOT / "output" / "v0.7_vs_v0.8_geometry.json"

items = {}
for source in sorted(PAIR_DIR.glob("*_拆板前.dxf")):
    stem = source.stem.replace("_拆板前", "")
    old = OLD_OUTPUT / f"{stem}_自动拆板_清洁1to1.dxf"
    if not old.exists():
        continue
    assembly = extract_bh_assembly(load_document(source), source_path=source)
    comparison = compare_bh_to_manual(assembly, old)
    items[stem] = {
        "ok": comparison.ok,
        "max_plate_hausdorff_mm": comparison.values["max_plate_hausdorff_mm"],
        "max_bbox_difference_mm": comparison.values["max_bbox_difference_mm"],
        "max_circular_cut_center_difference_mm": comparison.values["max_circular_cut_center_difference_mm"],
        "max_circular_cut_radius_difference_mm": comparison.values["max_circular_cut_radius_difference_mm"],
        "max_shaped_cut_hausdorff_mm": comparison.values["max_shaped_cut_hausdorff_mm"],
    }
report = {
    "version_from": "0.7.0",
    "version_to": "0.8.0",
    "sample_count": len(items),
    "all_equivalent": all(item["ok"] for item in items.values()),
    "maximum_plate_hausdorff_mm": max((item["max_plate_hausdorff_mm"] for item in items.values()), default=0.0),
    "items": items,
}
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({key: report[key] for key in ("sample_count", "all_equivalent", "maximum_plate_hausdorff_mm")}, ensure_ascii=False, indent=2))
