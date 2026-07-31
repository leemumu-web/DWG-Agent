from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.cad_processing import interface as cad_interface
from app.modules.cad_processing import tasks as cad_tasks
from app.modules.jobs import dispatch
from app.modules.jobs.dispatch import PermanentDispatchError
from app.modules.jobs.outbox import DispatchLease
from app.platform.config.constants import PIPELINE_DXF, TASK_DWG_TO_DXF
from tests.support.paths import REPO_ROOT

DISPATCHER = REPO_ROOT / "backend/app/modules/jobs/dispatcher.py"
ENQUEUE_INTERFACES = (
    REPO_ROOT / "backend/app/modules/cad_processing/interface.py",
    REPO_ROOT / "backend/app/modules/dxf_classification/interface.py",
    REPO_ROOT / "backend/app/modules/dxf_splitting/interface.py",
    REPO_ROOT / "backend/app/modules/excel_processing/interface.py",
)


def test_dispatcher_is_a_single_purpose_resilient_process():
    source = DISPATCHER.read_text(encoding="utf-8")
    assert "def run_forever(" in source
    assert "drain_once(factory)" in source
    assert "if __name__ == \"__main__\"" in source
    assert "time.sleep(idle_seconds)" in source
    assert "time.sleep(error_seconds)" in source


def test_all_job_enqueue_boundaries_accept_stable_task_ids():
    for path in ENQUEUE_INTERFACES:
        source = path.read_text(encoding="utf-8")
        assert ".delay(" not in source
        assert ".apply_async(" in source
        assert "task_id=task_id" in source


def test_cad_enqueue_passes_stable_task_id_to_celery(monkeypatch):
    captured: list[dict[str, object]] = []

    def apply_async(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(id=kwargs["task_id"])

    monkeypatch.setattr(cad_tasks.convert_dwg_to_dxf_task, "apply_async", apply_async)

    result = cad_interface.enqueue_dwg_to_dxf_job(41, 3, task_id="dispatch-uid-1")

    assert result == "dispatch-uid-1"
    assert captured == [{"args": [41, 3], "task_id": "dispatch-uid-1"}]


def test_publish_conversion_batch_uses_one_stable_message_id(monkeypatch):
    captured: list[tuple[list[list[int]], str | None]] = []
    monkeypatch.setattr(
        cad_interface,
        "enqueue_dwg_to_dxf_batch",
        lambda jobs, *, task_id=None: captured.append((jobs, task_id)) or str(task_id),
    )
    lease = DispatchLease(
        dispatch_uid="dispatch-batch-1",
        lease_token="lease-1",
        mode="conversion_batch",
        task_type=TASK_DWG_TO_DXF,
        pipeline=PIPELINE_DXF,
        jobs=((7, 1), (8, 2)),
    )

    result = dispatch.publish_dispatch(lease)

    assert result == "dispatch-batch-1"
    assert captured == [([[7, 1], [8, 2]], "dispatch-batch-1")]


def test_publish_rejects_unknown_pipeline_before_broker_io(monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "enqueue_job",
        lambda *_args, **_kwargs: pytest.fail("invalid snapshot must not publish"),
    )
    lease = DispatchLease(
        dispatch_uid="dispatch-invalid-1",
        lease_token="lease-1",
        mode="single",
        task_type="framework_smoke_test",
        pipeline="unknown-pipeline",
        jobs=((7, 1),),
    )

    with pytest.raises(PermanentDispatchError):
        dispatch.publish_dispatch(lease)


def test_publish_rejects_task_pipeline_mismatch_before_broker_io(monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "enqueue_job",
        lambda *_args, **_kwargs: pytest.fail("mismatched snapshot must not publish"),
    )
    lease = DispatchLease(
        dispatch_uid="dispatch-mismatch-1",
        lease_token="lease-1",
        mode="single",
        task_type=TASK_DWG_TO_DXF,
        pipeline="excel_final",
        jobs=((7, 1),),
    )

    with pytest.raises(PermanentDispatchError):
        dispatch.publish_dispatch(lease)
