"""Small, durable file primitives shared by manufacturing artifact writers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


def fsync_directory(path: Path) -> None:
    """Persist directory metadata where the host exposes POSIX directory FDs."""

    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    directory_descriptor = os.open(path, os.O_RDONLY | directory_flag)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def write_json_atomic(path: Path, payload: object) -> None:
    """Durably replace one UTF-8 JSON artifact without exposing a partial file."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".pending",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(
                payload,
                temporary,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        fsync_directory(destination.parent)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
