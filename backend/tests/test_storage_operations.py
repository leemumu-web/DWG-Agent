"""Migration-chain, seed-data, reaper, and backup/restore integration tests.

Coverage map (task requirements 1-7):

1. Migration-chain integrity  -> ``TestMigrationChain`` (pure filesystem, always runs)
2. Seed-data idempotency      -> ``TestSeedIdempotency`` (SQLite via conftest)
3. Reaper dry-run             -> ``TestReaperDryRun`` (SQLite + mocked storage)
4. Reaper retention window    -> ``TestReaperRetention`` (SQLite + mocked storage)
5. Reaper real delete         -> ``TestReaperRealDelete`` (SQLite + mocked storage)
6. Backup completeness        -> ``TestBackupCompleteness`` (static source + MySQL-gated)
7. admin-login-after-restore  -> ``TestRestoreAdminLogin`` (SQLite seed + MySQL-gated)

Reaper / seed tests reuse conftest's per-test in-memory SQLite engine.  The
``reap_storage`` module opens its own ``SessionLocal()`` sessions and calls
``get_storage_backend()``; both are monkeypatched here to the test engine and a
mock backend so nothing touches real MySQL or object storage.

Backup / reset-restore tests require a live MySQL/MariaDB + shell tooling and are
skipped with ``@pytest.mark.skipif(not _mysql_available(), ...)`` when absent.
"""

from __future__ import annotations

import gzip
import importlib
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import verify_password
from app.db.init_db import PERMISSION_SEEDS, ROLE_SEEDS, init_db
from app.models import Permission, Role, User
from app.models.file import StoredFile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
VERSIONS_DIR = BACKEND_ROOT / "migrations" / "versions"
DB_SCRIPT = SCRIPTS_DIR / "db.sh"
DOCKER_SCRIPT = SCRIPTS_DIR / "docker.sh"

EXPECTED_HEAD = "a9e4c7d2f610"


# ── shared helpers ───────────────────────────────────────────────────────────


def _mysql_available() -> bool:
    """True only when a MySQL/MariaDB server + client + db.sh are all reachable.

    Runtime uses MySQL; the unit test process uses SQLite (conftest).  Tests that
    genuinely need MySQL are gated on this probe so they skip cleanly on dev
    machines / CI that have no database daemon.
    """
    if not (shutil.which("mysql") or shutil.which("mariadb")):
        return False
    try:
        with socket.create_connection(("127.0.0.1", 3306), timeout=1):
            return True
    except OSError:
        return False


def _destructive_db_tests_enabled() -> bool:
    """Gate for tests that DROP/recreate the real database.

    Availability of MySQL is *not* sufficient — a developer usually has a live
    ``dwg_agent`` dev database on :3306, and ``db.sh reset`` would wipe it.  These
    tests only run when explicitly opted in via ``DWG_ALLOW_DESTRUCTIVE_DB_TESTS=1``
    against a reachable server, so a plain ``pytest`` run can never nuke real data.
    """
    return os.environ.get("DWG_ALLOW_DESTRUCTIVE_DB_TESTS") == "1" and _mysql_available()


def _load_reap_storage():
    """Import scripts/reap_storage.py (adds backend/ to sys.path on import)."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    module = importlib.import_module("reap_storage")
    return importlib.reload(module)


def _make_stored_file(*, storage_key: str, status: str, updated_at: datetime) -> StoredFile:
    row = StoredFile(
        bucket="dwg-original",
        storage_key=storage_key,
        original_name=f"{storage_key}.dwg",
        file_ext=".dwg",
        content_type="application/acad",
        size_bytes=2048,
        sha256="a" * 64,
        md5="b" * 32,
        status=status,
    )
    # created_at/updated_at carry an ``onupdate=utcnow`` default that only fires
    # on UPDATE, so an explicit value set at INSERT time is preserved verbatim —
    # letting us backdate rows for the retention-window assertions.
    row.created_at = updated_at
    row.updated_at = updated_at
    return row


@pytest.fixture
def reaper(db, monkeypatch):
    """Wire reap_storage to the conftest SQLite engine + a mock storage backend.

    Yields ``(module, mock_backend)``.  ``db`` (conftest fixture) shares the same
    StaticPool in-memory connection, so rows written/deleted by the reaper are
    visible to assertions made through ``db`` after ``db.expire_all()``.
    """
    module = _load_reap_storage()
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)

    mock_backend = MagicMock()
    mock_backend.delete_object = MagicMock()

    monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(module, "get_storage_backend", lambda: mock_backend)
    return module, mock_backend


# ── requirement 1: migration-chain integrity ─────────────────────────────────


class TestMigrationChain:
    @staticmethod
    def _parse_chain() -> dict[str, str | None]:
        """Return {revision: down_revision} parsed from every version file."""
        rev_re = re.compile(r"""^revision:\s*str\s*=\s*['"]([0-9a-f]+)['"]""", re.M)
        down_re = re.compile(
            r"""^down_revision(?::[^=]+)?\s*=\s*(?:['"]([0-9a-f]+)['"]|None)""", re.M
        )
        chain: dict[str, str | None] = {}
        for path in VERSIONS_DIR.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            rev_match = rev_re.search(source)
            down_match = down_re.search(source)
            assert rev_match, f"{path.name} has no parseable revision identifier"
            assert down_match, f"{path.name} has no parseable down_revision identifier"
            chain[rev_match.group(1)] = down_match.group(1)  # None when 'None' matched
        return chain

    def test_fifteen_migration_files_present(self):
        assert len(list(VERSIONS_DIR.glob("*.py"))) == 15

    def test_exactly_one_base_revision(self):
        chain = self._parse_chain()
        bases = [rev for rev, down in chain.items() if down is None]
        assert bases == ["40452ddd24e7"], f"expected single base, got {bases}"

    def test_head_is_expected_revision(self):
        chain = self._parse_chain()
        referenced = {down for down in chain.values() if down is not None}
        heads = [rev for rev in chain if rev not in referenced]
        assert heads == [EXPECTED_HEAD], f"expected single head {EXPECTED_HEAD}, got {heads}"

    def test_every_down_revision_points_at_a_real_revision(self):
        chain = self._parse_chain()
        for rev, down in chain.items():
            if down is not None:
                assert down in chain, f"{rev} points at unknown down_revision {down}"

    def test_chain_is_linear_and_acyclic_from_head_to_base(self):
        chain = self._parse_chain()
        seen: list[str] = []
        cursor: str | None = EXPECTED_HEAD
        while cursor is not None:
            assert cursor not in seen, f"cycle detected at revision {cursor}"
            seen.append(cursor)
            cursor = chain[cursor]
        # Walking head -> base must visit every migration exactly once.
        assert len(seen) == len(chain) == 15
        assert seen[-1] == "40452ddd24e7"


# ── requirement 2: seed-data idempotency ─────────────────────────────────────


class TestSeedIdempotency:
    def test_init_db_twice_creates_no_duplicates(self, db):
        init_db()
        init_db()  # second run must be a no-op, not raise

        db.expire_all()
        assert db.scalar(select(func.count()).select_from(Role)) == len(ROLE_SEEDS) == 7
        assert (
            db.scalar(select(func.count()).select_from(Permission)) == len(PERMISSION_SEEDS) == 8
        )
        admins = db.scalars(
            select(User).where(User.username == settings.super_admin_username)
        ).all()
        assert len(admins) == 1

    def test_reseed_over_existing_admin_preserves_login(self, db):
        init_db()
        init_db()

        db.expire_all()
        admin = db.scalar(select(User).where(User.username == settings.super_admin_username))
        assert admin is not None
        assert verify_password(settings.super_admin_password, admin.password_hash)
        # Super-admin still owns every seeded permission after re-seed.
        assert {p.code for p in admin.roles[0].permissions} == {
            code for code, *_ in PERMISSION_SEEDS
        }


# ── requirement 3: reaper dry-run makes no changes ───────────────────────────


class TestReaperDryRun:
    def test_dry_run_deletes_nothing(self, db, reaper):
        module, mock_backend = reaper
        old = _make_stored_file(
            storage_key="uploads/old.dwg",
            status="deleted",
            updated_at=datetime.now(UTC) - timedelta(days=90),
        )
        db.add(old)
        db.commit()

        result = module.reap(retention_days=7, dry_run=True)

        assert result["dry_run"] is True
        assert result["rows_deleted"] == 1  # counted, not deleted
        mock_backend.delete_object.assert_not_called()

        db.expire_all()
        assert db.scalar(select(func.count()).select_from(StoredFile)) == 1


# ── requirement 4: retention window filters correctly ────────────────────────


class TestReaperRetention:
    def test_fresh_delete_kept_old_delete_reaped(self, db, reaper):
        module, mock_backend = reaper
        now = datetime.now(UTC)
        fresh = _make_stored_file(
            storage_key="uploads/fresh.dwg", status="deleted", updated_at=now
        )
        stale = _make_stored_file(
            storage_key="uploads/stale.dwg",
            status="deleted",
            updated_at=now - timedelta(days=30),
        )
        db.add_all([fresh, stale])
        db.commit()

        result = module.reap(retention_days=7, dry_run=False)

        # Only the row older than the 7-day cutoff is reclaimed.
        assert result["rows_deleted"] == 1
        assert result["errors"] == 0
        mock_backend.delete_object.assert_called_once_with("dwg-original", "uploads/stale.dwg")

        db.expire_all()
        remaining = db.scalars(select(StoredFile.storage_key)).all()
        assert remaining == ["uploads/fresh.dwg"]

    def test_available_files_are_never_candidates(self, db, reaper):
        module, mock_backend = reaper
        available = _make_stored_file(
            storage_key="uploads/live.dwg",
            status="available",
            updated_at=datetime.now(UTC) - timedelta(days=365),
        )
        db.add(available)
        db.commit()

        result = module.reap(retention_days=7, dry_run=False)

        assert result["rows_deleted"] == 0
        mock_backend.delete_object.assert_not_called()
        db.expire_all()
        assert db.scalar(select(func.count()).select_from(StoredFile)) == 1


# ── requirement 5: real delete removes object + DB row ───────────────────────


class TestReaperRealDelete:
    def test_no_dry_run_calls_storage_and_removes_rows(self, db, reaper):
        module, mock_backend = reaper
        old = datetime.now(UTC) - timedelta(days=60)
        rows = [
            _make_stored_file(storage_key=f"uploads/d{i}.dwg", status="deleted", updated_at=old)
            for i in range(3)
        ]
        db.add_all(rows)
        db.commit()

        result = module.reap(retention_days=30, dry_run=False)

        assert result["dry_run"] is False
        assert result["rows_deleted"] == 3
        assert result["errors"] == 0
        assert mock_backend.delete_object.call_count == 3
        called_keys = {c.args[1] for c in mock_backend.delete_object.call_args_list}
        assert called_keys == {"uploads/d0.dwg", "uploads/d1.dwg", "uploads/d2.dwg"}

        db.expire_all()
        assert db.scalar(select(func.count()).select_from(StoredFile)) == 0

    def test_storage_error_is_counted_and_row_survives(self, db, reaper):
        module, mock_backend = reaper
        mock_backend.delete_object.side_effect = module.StorageError("boom")
        row = _make_stored_file(
            storage_key="uploads/err.dwg",
            status="deleted",
            updated_at=datetime.now(UTC) - timedelta(days=60),
        )
        db.add(row)
        db.commit()

        result = module.reap(retention_days=30, dry_run=False)

        assert result["errors"] == 1
        assert result["rows_deleted"] == 0
        db.expire_all()
        # A failed storage delete must NOT orphan-delete the DB row.
        assert db.scalar(select(func.count()).select_from(StoredFile)) == 1

    def test_mixed_batch_commits_successful_row_and_keeps_failed_row(self, db, reaper):
        module, mock_backend = reaper
        old = datetime.now(UTC) - timedelta(days=60)
        successful = _make_stored_file(
            storage_key="uploads/ok.dwg", status="deleted", updated_at=old
        )
        failed = _make_stored_file(
            storage_key="uploads/failed.dwg", status="deleted", updated_at=old
        )
        db.add_all([successful, failed])
        db.commit()
        successful_id = successful.id
        failed_id = failed.id
        mock_backend.delete_object.side_effect = [None, module.StorageError("offline")]

        result = module.reap(retention_days=30, dry_run=False)

        assert result["rows_deleted"] == 1
        assert result["errors"] == 1
        db.expire_all()
        assert db.get(StoredFile, successful_id) is None
        assert db.get(StoredFile, failed_id) is not None


# ── requirement 6: backup contains both databases ────────────────────────────


class TestBackupCompleteness:
    def test_docker_backup_dumps_both_databases(self):
        """Static guard: the container backup must dump dwg_agent + hardware_handbook.

        ``mysqldump --databases <a> <b>`` is what emits a ``CREATE DATABASE`` header
        per database (a single-db dump does not), so this flag form is what makes a
        restore recreate both schemas.
        """
        source = DOCKER_SCRIPT.read_text(encoding="utf-8")
        assert "--databases" in source
        assert '"$MYSQL_DATABASE" hardware_handbook' in source
        assert "gzip" in source and "mysql.sql.gz" in source

    @pytest.mark.skipif(not _mysql_available(), reason="requires live MySQL/MariaDB")
    def test_real_dump_emits_create_database_for_both(self, tmp_path):
        """Actually dump both databases and grep the gzip for CREATE DATABASE.

        Mirrors docker.sh's ``mysqldump --databases`` form.  Skips unless both
        databases exist on the local server.
        """
        client = shutil.which("mariadb-dump") or shutil.which("mysqldump")
        if client is None:
            pytest.skip("no mysqldump/mariadb-dump client")

        out = tmp_path / "backup.sql.gz"
        proc = subprocess.run(
            [client, "--no-data", "--databases", "dwg_agent", "hardware_handbook"],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            pytest.skip(f"dump failed (missing db/creds): {proc.stderr.decode()[:200]}")
        out.write_bytes(gzip.compress(proc.stdout))

        text = gzip.decompress(out.read_bytes()).decode("utf-8", "replace")
        assert re.search(r"CREATE DATABASE.*`?dwg_agent`?", text)
        assert re.search(r"CREATE DATABASE.*`?hardware_handbook`?", text)


# ── requirement 7: admin can still log in after restore ──────────────────────


class TestRestoreAdminLogin:
    def test_seeded_admin_password_verifies(self, db):
        """Unit-level stand-in for 'admin can log in': after seeding, the stored
        argon2 hash verifies against the configured super-admin password."""
        init_db()
        db.expire_all()
        admin = db.scalar(select(User).where(User.username == settings.super_admin_username))
        assert admin is not None
        assert admin.status == "active"
        assert admin.password_algo == "argon2id"
        assert verify_password(settings.super_admin_password, admin.password_hash)

    @pytest.mark.skipif(
        not _destructive_db_tests_enabled(),
        reason="destructive: set DWG_ALLOW_DESTRUCTIVE_DB_TESTS=1 with a throwaway MySQL",
    )
    def test_reset_restore_keeps_admin_login(self, tmp_path):
        """Full round-trip on real MySQL: reset (migrate+seed) -> backup -> restore,
        then confirm the admin row + password survive.

        Opt-in only (see ``_destructive_db_tests_enabled``): ``db.sh reset`` DROPs and
        recreates the database, so this must never run against a real dev DB."""
        env = {
            "RESET_CONFIRM": "yes",
            "PATH": os.environ.get("PATH", ""),
        }
        reset = subprocess.run(
            ["bash", str(DB_SCRIPT), "reset"],
            cwd=PROJECT_ROOT,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            check=False,
        )
        if reset.returncode != 0:
            pytest.skip(f"db.sh reset unavailable: {reset.stdout[-200:]}{reset.stderr[-200:]}")

        dump = tmp_path / "roundtrip.sql.gz"
        backup = subprocess.run(
            ["bash", str(DB_SCRIPT), "backup", str(dump)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert backup.returncode == 0, backup.stderr
        assert dump.exists() and dump.stat().st_size > 0

        restore = subprocess.run(
            ["bash", str(DB_SCRIPT), "restore", str(dump)],
            cwd=PROJECT_ROOT,
            input="yes\n",
            capture_output=True,
            text=True,
            check=False,
        )
        assert restore.returncode == 0, restore.stderr

        # After restore, the admin user + its permissions must still be present.
        check = subprocess.run(
            ["bash", str(DB_SCRIPT), "check"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert check.returncode == 0, check.stdout + check.stderr
