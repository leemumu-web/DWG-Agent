from __future__ import annotations

import json
import sys

from tests.support.paths import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from scripts.architecture.snapshot_contracts import (  # noqa: E402
    SNAPSHOT_PATH,
    build_contract_snapshot,
)


def test_runtime_contract_matches_committed_snapshot() -> None:
    actual = build_contract_snapshot()
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert actual == expected


def test_contract_snapshot_locks_every_public_surface() -> None:
    snapshot = build_contract_snapshot()

    assert len(snapshot["http_paths"]) == 183
    assert len(snapshot["http_operations"]) == 211
    assert len(snapshot["orm_tables"]) == 48
    assert len(snapshot["celery_tasks"]) == 16
    assert len(snapshot["celery_task_routes"]) == 14
    assert "app.workers.tasks_agent.* -> agent" in snapshot["celery_task_routes"]
    assert "app.workers.tasks_cad.* -> cad" in snapshot["celery_task_routes"]
    assert "app.workers.tasks_dispatch.* -> dispatch" in snapshot["celery_task_routes"]
    assert (
        "app.workers.tasks_dxf_split.* -> dxf_split"
        in snapshot["celery_task_routes"]
    )
    assert snapshot["alembic_heads"] == ["a4c8e1f2b730"]
    assert "/workflows" in snapshot["frontend_routes"]
    assert "/files/dwg2dxf" in snapshot["frontend_routes"]
    assert "backend-api" in snapshot["compose_services"]
    assert "worker-dxf-split" in snapshot["compose_services"]
    assert "nginx" in snapshot["compose_services"]


def test_snapshot_lists_are_sorted_and_unique() -> None:
    snapshot = build_contract_snapshot()

    for key, values in snapshot.items():
        if isinstance(values, list):
            assert values == sorted(set(values)), key
