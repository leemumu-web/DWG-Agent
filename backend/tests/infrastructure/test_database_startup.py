from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_database_startup_migrates_before_schema_check() -> None:
    content = (PROJECT_ROOT / "scripts/lib/database.sh").read_text(encoding="utf-8")

    migrate_call = 'scripts/db.sh" migrate'
    check_call = 'scripts/db.sh" check'
    assert migrate_call in content
    assert content.index(migrate_call) < content.index(check_call)
