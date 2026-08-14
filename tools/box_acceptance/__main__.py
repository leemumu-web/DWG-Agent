from __future__ import annotations

import argparse
from pathlib import Path

from .runner import run_acceptance, write_markdown_report, write_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 BH/BOX 外部生产标准验收")
    parser.add_argument("--sample-root", type=Path, required=True)
    parser.add_argument("--classification-manifest", type=Path, required=True)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()

    def progress(index, count, result) -> None:
        print(
            f"[{index:02d}/{count}] {result.sample_id}: "
            f"internal={result.internal_disposition or '-'} "
            f"external={result.status.value}",
            flush=True,
        )

    report = run_acceptance(
        sample_root=args.sample_root,
        classification_manifest=args.classification_manifest,
        snapshot_root=args.snapshot_root,
        artifact_root=args.artifact_root,
        progress=progress,
    )
    write_report(report, args.report_output)
    write_markdown_report(report, args.markdown_output)
    return 0 if report["source_evidence_unchanged"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
