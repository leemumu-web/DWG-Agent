"""Bounded ODA directory conversion shared by both batch directions."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from app.platform.config.settings import settings


@dataclass(frozen=True)
class OdaShardFailure:
    source_names: tuple[str, ...]
    error: Exception


class OdaGroupConversionError(RuntimeError):
    """One or more ODA shards failed after other shards may have succeeded."""

    def __init__(self, results: list, failures: list[OdaShardFailure]):
        super().__init__(f"{len(failures)} ODA shard(s) failed")
        self.results = results
        self.failures = tuple(failures)
        self.failed_source_names = tuple(
            sorted(name for failure in failures for name in failure.source_names)
        )


def _convert_oda_group(
    *,
    staged_paths: list[Path],
    output_root: Path,
    convert_directory,
    converter_kwargs: dict,
    on_shard_complete: Callable[[list, int, int], None] | None = None,
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
        results = list(batch.results)
        if on_shard_complete is not None:
            on_shard_complete(results, 1, 1)
        return results

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

    results = []
    failures: list[OdaShardFailure] = []
    with ThreadPoolExecutor(max_workers=shard_count) as pool:
        futures = {
            pool.submit(run_shard, shard): shard
            for shard in shards
        }
        for completed_count, future in enumerate(as_completed(futures), start=1):
            source_dir, _ = futures[future]
            try:
                shard_results = list(future.result().results)
            except Exception as exc:
                failures.append(
                    OdaShardFailure(
                        source_names=tuple(
                            sorted(path.name for path in source_dir.iterdir() if path.is_file())
                        ),
                        error=exc,
                    )
                )
                continue
            results.extend(shard_results)
            if on_shard_complete is not None:
                # This loop runs in the caller thread. The callback may safely use
                # the caller's SQLAlchemy Session without crossing worker threads.
                on_shard_complete(shard_results, completed_count, shard_count)
    if failures:
        raise OdaGroupConversionError(results, failures)
    return results


__all__ = [
    "OdaGroupConversionError",
    "OdaShardFailure",
    "_convert_oda_group",
]
