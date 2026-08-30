from __future__ import annotations

import argparse
import json
from pathlib import Path

from .assembly import solve_complete_box
from .metadata import resolve_box_metadata
from .source_ir import build_source_ir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect source-only BOX compiler evidence without code generation."
    )
    parser.add_argument("input", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = build_source_ir(args.input)
    metadata = resolve_box_metadata(source)
    search = solve_complete_box(source, metadata)
    best = search.best
    print(
        json.dumps(
            {
                "input": str(args.input.resolve()),
                "source_geometry_fingerprint": source.geometry_fingerprint,
                "groups": len(source.groups),
                "entities": len(source.entities),
                "metadata": {
                    "part_number": metadata.member_mark.value,
                    "profile": metadata.profile.value.canonical,
                    "material": metadata.material.value,
                    "nominal_length_mm": metadata.nominal_length.value,
                },
                "search_complete": search.search_complete,
                "hypothesis_count": len(search.hypotheses),
                "selected_assignment": best.assignment.signature,
                "proof_report": best.proof_report.to_dict(),
                "manufacturing_fingerprint": best.mir.fingerprint,
                "physical_plates": [
                    {
                        "role": plate.role.value,
                        "thickness_mm": plate.thickness_mm,
                        "circular_cut_count": len(plate.circular_cuts),
                    }
                    for plate in best.mir.physical_plates
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
