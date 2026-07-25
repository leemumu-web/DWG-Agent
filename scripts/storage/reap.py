#!/usr/bin/env python3
"""Reclaim storage objects for soft-deleted files and crash-orphaned uploads.

Soft-delete (``files_api.delete_file``) only sets ``StoredFile.status = 'deleted'``
and never removes the underlying storage object.  Crash-orphaned objects are uploads
whose storage PUT succeeded but the DB transaction rolled back or the process died
before commit — they have no matching ``StoredFile`` row at all.

Usage
-----
    cd backend
    uv run python ../scripts/storage/reap.py --dry-run
    uv run python ../scripts/storage/reap.py --retention-days 30
    uv run python ../scripts/storage/reap.py --retention-days 0 --no-dry-run

Also available via::

    bash scripts/db.sh reap-storage [--dry-run] [--retention-days N]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Ensure backend/ is on sys.path so we can import the app modules.
_backend = Path(__file__).resolve().parents[2] / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from sqlalchemy import func, select  # noqa: E402 - backend path is injected above

from app.modules.files.interface import (  # noqa: E402 - backend path is injected above
    StoredFile,
    get_storage_backend,
)
from app.platform.config.settings import settings  # noqa: E402 - backend path is injected above
from app.platform.database.session import (  # noqa: E402 - backend path is injected above
    SessionLocal,
)
from app.platform.storage.base import StorageError  # noqa: E402 - backend path is injected above

logger = logging.getLogger("reap_storage")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


BATCH_SIZE = int(os.environ.get("REAP_BATCH_SIZE", "100"))

# ── helpers ────────────────────────────────────────────────────────────────


def _human_size(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def _count_orphans() -> int:
    """Count storage objects that have no matching StoredFile row.

    Only implemented for the local backend (MinIO enumeration would be
    expensive for large buckets).
    """
    backend = get_storage_backend()
    try:
        local_root = backend.root  # type: ignore[attr-defined]
    except AttributeError:
        return 0  # MinIO — skip (would need bucket_object_counts + per-key diff)

    count = 0
    with SessionLocal() as db:
        all_keys = set(
            db.execute(select(StoredFile.bucket, StoredFile.storage_key)).all()
        )
    for bucket_dir in local_root.iterdir():
        if not bucket_dir.is_dir():
            continue
        for fpath in bucket_dir.rglob("*"):
            if not fpath.is_file():
                continue
            rel = fpath.relative_to(local_root).as_posix()
            # rel looks like "dwg-original/uploads/a1b2c3.dwg"
            # storage_key = "uploads/a1b2c3.dwg" (bucket stripped)
            parts = rel.split("/", 1)
            storage_key = parts[1] if len(parts) > 1 else rel
            if (parts[0], storage_key) not in all_keys:
                count += 1
    return count


# ── main reaper ─────────────────────────────────────────────────────────────


def reap(
    *,
    retention_days: int = 30,
    dry_run: bool = True,
    include_orphans: bool = False,
) -> dict:
    """Delete storage objects for soft-deleted files past retention.

    Returns a summary dict with counts and bytes reclaimed.
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    backend = get_storage_backend()
    total_rows = 0
    total_bytes = 0
    errors = 0
    orphan_count = 0

    logger.info(
        "storage backend: %s, retention: %s days (cutoff %s), dry-run: %s",
        settings.storage_backend,
        retention_days,
        cutoff.isoformat(),
        dry_run,
    )

    # ── Phase 1: soft-deleted files ──────────────────────────────────
    with SessionLocal() as db:
        query = (
            select(StoredFile)
            .where(
                StoredFile.status == "deleted",
                StoredFile.purged_at.is_(None),
                StoredFile.updated_at < cutoff,
            )
            .order_by(StoredFile.id)
        )

        total_candidates = db.scalar(
            select(func.count()).select_from(query.subquery())
        ) or 0

        logger.info(
            "phase 1 — soft-deleted rows past cutoff: %d", total_candidates
        )

        rows = db.scalars(query).all()
        for i, row in enumerate(rows):
            if dry_run:
                total_rows += 1
                total_bytes += row.size_bytes
                continue

            try:
                backend.delete_object(row.bucket, row.storage_key)
                db.delete(row)
                db.commit()
                total_rows += 1
                total_bytes += row.size_bytes
            except StorageError:
                db.rollback()
                logger.exception(
                    "failed to delete storage object %s/%s (id=%d)",
                    row.bucket, row.storage_key, row.id,
                )
                errors += 1
                continue

            if (i + 1) % BATCH_SIZE == 0:
                logger.info(
                    "phase 1 progress: %d/%d rows (%.1f%%)",
                    i + 1, len(rows),
                    (i + 1) / len(rows) * 100 if len(rows) else 0,
                )

        if not dry_run:
            logger.info(
                "phase 1 complete: %d rows, %s reclaimed, %d errors",
                total_rows,
                _human_size(total_bytes),
                errors,
            )

    # ── Phase 2: crash-orphaned objects (local backend only) ─────────
    if include_orphans and settings.storage_backend == "local":
        logger.info("phase 2 — scanning for crash-orphaned objects ...")
        orphan_count = _count_orphans()
        logger.info(
            "phase 2 — orphaned objects detected: %d (dry-run, not deleted)", orphan_count
        )
        # Actual deletion of orphans requires more safety checks
        # (e.g. checking the file mtime is > 1 hour old to avoid
        # deleting in-flight uploads).  For now, just report.

    return {
        "rows_deleted": total_rows,
        "bytes_reclaimed": total_bytes,
        "human_bytes": _human_size(total_bytes),
        "errors": errors,
        "orphans_detected": orphan_count,
        "dry_run": dry_run,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reclaim storage objects for soft-deleted files",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=int(os.environ.get("REAP_RETENTION_DAYS", "30")),
        help="Delete rows whose updated_at is older than N days (default: 30)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Report what would be deleted without executing (default)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Actually delete objects and rows",
    )
    parser.add_argument(
        "--include-orphans",
        action="store_true",
        help="Also scan for crash-orphaned objects with no DB row",
    )
    args = parser.parse_args()

    if not args.dry_run and args.retention_days < 1:
        logger.warning(
            "retention_days=%d — this will hard-delete ALL soft-deleted files immediately.",
            args.retention_days,
        )
        if os.environ.get("REAP_CONFIRM") != "yes":
            print("Set REAP_CONFIRM=yes to proceed with retention_days < 1, or use --dry-run.")
            return 1

    result = reap(
        retention_days=args.retention_days,
        dry_run=args.dry_run,
        include_orphans=args.include_orphans,
    )

    print()
    print("─" * 60)
    if result["dry_run"]:
        print("  DRY RUN — no changes made")
    print(f"  soft-deleted rows processed: {result['rows_deleted']}")
    print(f"  storage reclaimed:           {result['human_bytes']}")
    print(f"  errors:                      {result['errors']}")
    print(f"  orphaned objects detected:   {result['orphans_detected']}")
    print("─" * 60)

    return 0 if result["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
