from __future__ import annotations

import tempfile
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from app.db.session import db_health


def _create_engine_with_pragmas(db_url: str):
    """Create a SQLite engine with the same pragma listener as session.py."""
    engine = create_engine(db_url)

    @event.listens_for(Engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.close()

    return engine


# ---------------------------------------------------------------------------
# Pragma enforcement
# ---------------------------------------------------------------------------
class TestSQLitePragmas:
    def test_wal_mode_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            engine = _create_engine_with_pragmas(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                result = conn.execute(text("PRAGMA journal_mode;")).scalar()
                assert result.lower() == "wal"

    def test_foreign_keys_enabled(self):
        engine = _create_engine_with_pragmas("sqlite:///:memory:")
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA foreign_keys;")).scalar()
            assert result == 1

    def test_busy_timeout_set(self):
        engine = _create_engine_with_pragmas("sqlite:///:memory:")
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA busy_timeout;")).scalar()
            assert result == 5000

    def test_wal_files_created_on_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "wal-test.db"
            engine = _create_engine_with_pragmas(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                conn.execute(text("CREATE TABLE t (x INTEGER)"))
                conn.execute(text("INSERT INTO t VALUES (1)"))
                conn.commit()
            # WAL and SHM files should exist
            wal = Path(str(db_path) + "-wal")
            shm = Path(str(db_path) + "-shm")
            assert wal.exists(), f"expected {wal}"
            assert shm.exists(), f"expected {shm}"

    def test_multiple_connections_share_pragmas(self):
        engine = _create_engine_with_pragmas("sqlite:///:memory:")
        conn1 = engine.connect()
        conn2 = engine.connect()
        fk1 = conn1.execute(text("PRAGMA foreign_keys;")).scalar()
        fk2 = conn2.execute(text("PRAGMA foreign_keys;")).scalar()
        assert fk1 == 1
        assert fk2 == 1
        conn1.close()
        conn2.close()


# ---------------------------------------------------------------------------
# Foreign key enforcement
# ---------------------------------------------------------------------------
class TestForeignKeyEnforcement:
    def test_raises_integrity_error_on_orphan(self):
        engine = _create_engine_with_pragmas("sqlite:///:memory:")
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
            conn.execute(
                text(
                    "CREATE TABLE child ("
                    "  id INTEGER PRIMARY KEY,"
                    "  parent_id INTEGER REFERENCES parent(id)"
                    ")"
                )
            )
            conn.commit()
            with conn.begin():
                conn.execute(text("INSERT INTO parent (id) VALUES (1)"))

            import sqlalchemy.exc

            with conn.begin():
                conn.execute(text("INSERT INTO child (id, parent_id) VALUES (1, 1)"))  # ok
            try:
                with conn.begin():
                    conn.execute(text("INSERT INTO child (id, parent_id) VALUES (2, 999)"))
                raise AssertionError("Expected IntegrityError")
            except sqlalchemy.exc.IntegrityError:
                pass  # expected — SQLAlchemy wraps the sqlite3 error

    def test_cascade_delete_works(self):
        """Verify that foreign key enforcement allows cascade deletes."""
        engine = _create_engine_with_pragmas("sqlite:///:memory:")
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
            conn.execute(
                text(
                    "CREATE TABLE child ("
                    "  id INTEGER PRIMARY KEY,"
                    "  parent_id INTEGER REFERENCES parent(id) ON DELETE CASCADE"
                    ")"
                )
            )
            conn.commit()
            conn.execute(text("INSERT INTO parent VALUES (1)"))
            conn.execute(text("INSERT INTO child VALUES (1, 1)"))
            conn.commit()
            conn.execute(text("DELETE FROM parent WHERE id=1"))
            conn.commit()
            child_count = conn.execute(text("SELECT COUNT(*) FROM child")).scalar()
            assert child_count == 0


# ---------------------------------------------------------------------------
# db_health()
# ---------------------------------------------------------------------------
class TestDbHealth:
    def test_ok(self):
        result = db_health()
        assert result["status"] == "ok"
        assert "reachable" in result["message"]
