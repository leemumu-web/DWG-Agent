from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from app.platform.config.settings import settings


def convert_dwg_directory(inputs: Mapping[int, Path], output_dir: Path) -> dict[int, Path]:
    """Convert one remnant batch with exactly one ODA directory invocation."""
    if not inputs:
        return {}
    source_dirs = {path.parent.resolve() for path in inputs.values()}
    if len(source_dirs) != 1:
        raise ValueError("all remnant DWG inputs must share one staging directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    from dwg_converter import convert_directory

    batch = convert_directory(
        source_dir=next(iter(source_dirs)),
        target_dir=output_dir,
        version=settings.oda_converter_version,
        audit=settings.oda_converter_audit,
        timeout=settings.oda_converter_timeout,
        retries=settings.oda_converter_retries,
    )
    by_stem = {path.stem: item_id for item_id, path in inputs.items()}
    outputs: dict[int, Path] = {}
    for result in batch.results:
        if result.success and result.target is not None and result.source.stem in by_stem:
            outputs[by_stem[result.source.stem]] = Path(result.target)
    return outputs
