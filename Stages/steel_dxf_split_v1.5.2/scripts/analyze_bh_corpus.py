from __future__ import annotations

import json
from pathlib import Path

from steel_dxf_split.bh_corpus import analyze_bh_corpus, corpus_report_markdown

ROOT = Path(__file__).resolve().parents[1]
PAIR_DIR = ROOT / "samples" / "bh_pairs"
OUTPUT = ROOT / "output"
OUTPUT.mkdir(parents=True, exist_ok=True)
report = analyze_bh_corpus(PAIR_DIR)
(OUTPUT / "BH语义编译器语料审计.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
(OUTPUT / "BH语义编译器语料审计.md").write_text(
    corpus_report_markdown(report), encoding="utf-8"
)
print(json.dumps({
    "sample_count": report["sample_count"],
    "all_validation_ok": report["all_validation_ok"],
    "all_supervised_ok": report["all_supervised_ok"],
    "maximum_hausdorff_mm": report["supervision"]["max_plate_hausdorff_mm"],
    "minimum_confidence": report["confidence"]["minimum"],
}, ensure_ascii=False, indent=2))
