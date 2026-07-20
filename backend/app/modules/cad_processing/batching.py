"""Bounded ODA directory conversion shared by both batch directions."""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.platform.config.settings import settings


def _convert_oda_group(
    *,
    staged_paths: list[Path],
    output_root: Path,
    convert_directory,
    converter_kwargs: dict,
) -> list:
    """Convert one version group using a measured, bounded number of ODA shards."""
    if not staged_paths:
        return []
    shard_count = min(
        settings.cad_batch_max_shards,
        max(
            1,
            (len(staged_paths) + settings.cad_batch_min_files_per_shard - 1)
            // settings.cad_batch_min_files_per_shard,
        ),
    )
    if shard_count == 1:
        batch = convert_directory(
            source_dir=staged_paths[0].parent,
            target_dir=output_root,
            **converter_kwargs,
        )
        return list(batch.results)

    shards: list[tuple[Path, Path]] = []
    for index in range(shard_count):
        source_dir = output_root / f"shard-{index}" / "input"
        target_dir = output_root / f"shard-{index}" / "output"
        source_dir.mkdir(parents=True, exist_ok=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        shards.append((source_dir, target_dir))
    for index, staged_path in enumerate(staged_paths):
        shutil.copy2(staged_path, shards[index % shard_count][0] / staged_path.name)

    def run_shard(paths: tuple[Path, Path]):
        source_dir, target_dir = paths
        return convert_directory(
            source_dir=source_dir,
            target_dir=target_dir,
            **converter_kwargs,
        )

    with ThreadPoolExecutor(max_workers=shard_count) as pool:
        batches = list(pool.map(run_shard, shards))
    return [result for batch in batches for result in batch.results]


__all__ = ["_convert_oda_group"]
