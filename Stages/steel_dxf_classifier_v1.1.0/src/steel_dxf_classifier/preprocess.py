from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4


PRE_SPLIT_SUFFIX = "_拆板前"


class FilenamePreprocessError(RuntimeError):
    """Raised when DXF filename preprocessing cannot complete atomically."""


def _dxf_files(directory: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() == ".dxf"
        ),
        key=lambda path: (path.name.casefold(), path.name),
    )


def _target_for(source: Path) -> Path:
    stem = source.stem
    if not stem.endswith(PRE_SPLIT_SUFFIX):
        stem = f"{stem}{PRE_SPLIT_SUFFIX}"
    return source.with_name(f"{stem}.dxf")


def _rename_plan(directory: Path) -> list[tuple[Path, Path]]:
    sources = _dxf_files(directory)
    targets: dict[str, Path] = {}
    occupants = {path.name.casefold(): path for path in directory.iterdir()}
    source_set = set(sources)

    for source in sources:
        target = _target_for(source)
        key = target.name.casefold()
        previous = targets.get(key)
        if previous is not None and previous != source:
            raise FilenamePreprocessError(
                f"DXF filename collision: {previous.name} and {source.name} "
                f"both map to {target.name}"
            )
        occupant = occupants.get(key)
        if occupant is not None and occupant not in source_set:
            raise FilenamePreprocessError(
                f"DXF filename collision: target {target.name} is occupied"
            )
        targets[key] = source

    return [(source, _target_for(source)) for source in sources]


def preprocess_dxf_filenames(directory: str | Path) -> tuple[Path, ...]:
    """Normalize first-level DXF names transactionally and return sorted paths."""
    root = Path(directory)
    if not root.is_dir():
        raise FilenamePreprocessError(f"preprocess input is not a directory: {root}")

    plan = _rename_plan(root)
    changes = [(source, target) for source, target in plan if source != target]
    staged: list[tuple[Path, Path, Path]] = []
    promoted: list[tuple[Path, Path, Path]] = []

    try:
        for source, target in changes:
            temporary = root / f".dxf-preprocess-{uuid4().hex}"
            os.replace(source, temporary)
            staged.append((source, temporary, target))
        for record in staged:
            source, temporary, target = record
            os.replace(temporary, target)
            promoted.append((source, temporary, target))
    except OSError as error:
        rollback_errors: list[OSError] = []
        for source, _temporary, target in reversed(promoted):
            try:
                if target.exists():
                    os.replace(target, source)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        for source, temporary, _target in reversed(staged):
            try:
                if temporary.exists():
                    os.replace(temporary, source)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            raise FilenamePreprocessError(
                f"DXF filename preprocessing failed; rollback incomplete: {error}"
            ) from error
        raise FilenamePreprocessError(
            f"DXF filename preprocessing failed and was rolled back: {error}"
        ) from error

    return tuple(
        sorted(
            (target for _source, target in plan),
            key=lambda path: (path.name.casefold(), path.name),
        )
    )
