"""Boundary tests for workflow service — edge cases, validation, and state transitions.

Covers:
- Service-layer state-machine guards
- Schema validation edges
- Artifact / stage binding boundaries
- Concurrent / invalid transition rejection
- Recompute correctness for every terminal path"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile
from app.modules.identity.interface import User
from app.modules.jobs.interface import Job
from app.modules.projects.interface import Project, ProjectMember
from app.platform.http.exceptions import AppHTTPException
from app.schemas.workflow_schema import WorkflowCreate
from app.services.workflow_service import (
    attach_artifact,
    bind_stage_job,
    cancel_workflow,
    complete_manual_stage,
    create_workflow,
    recompute_workflow,
    start_workflow,
    sync_workflow_from_jobs,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _owner_project(db: Session, code: str = "WF-TEST") -> tuple[User, Project]:
    user = User(
        username=f"wf-test-{uuid4().hex[:8]}",
        password_hash="x",
        real_name=f"Test Owner {code}",
        status="active",
    )
    db.add(user)
    db.flush()
    project = Project(code=code, name=f"Project {code}", owner_id=user.id, status="active")
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, project_role="project_owner"))
    db.flush()
    return user, project


def _seed_file(db: Session) -> StoredFile:
    file = StoredFile(
        bucket="test-bucket",
        storage_key=f"test/{uuid4().hex}",
        original_name="test.dat",
        file_ext=".dat",
        size_bytes=128,
        sha256=uuid4().hex + uuid4().hex,
        status="available",
    )
    db.add(file)
    db.flush()
    return file


def _make_draft(db: Session, workflow_type: str = "file_delivery") -> tuple[User, Project, object]:
    user, project = _owner_project(db)
    workflow = create_workflow(
        db,
        WorkflowCreate(project_id=project.id, name="Boundary Test", workflow_type=workflow_type),
        created_by=user.id,
    )
    return user, project, workflow


# ── creation boundaries ──────────────────────────────────────────────────────


def test_blanks_name_is_rejected(db: Session):
    user, project = _owner_project(db)
    for blank in ("", "   "):
        with pytest.raises((ValueError, AppHTTPException)):
            create_workflow(
                db,
                WorkflowCreate(project_id=project.id, name=blank, workflow_type="file_delivery"),
                created_by=user.id,
            )


def test_unknown_workflow_type_is_rejected(db: Session):
    user, project = _owner_project(db)
    with pytest.raises((ValueError, AppHTTPException)):
        create_workflow(
            db,
            WorkflowCreate(project_id=project.id, name="Invalid", workflow_type="cad_pipeline"),
            created_by=user.id,
        )


def test_excel_delivery_stages_are_correct_and_skip_cad(db: Session):
    _, _, workflow = _make_draft(db, "excel_delivery")
    codes = [stage.stage_code for stage in workflow.stages]
    assert codes == ["source_upload", "excel_process", "quality_review", "delivery"]
    cad_words = {"cad", "agent", "split", "classify", "extract"}
    for stage in workflow.stages:
        assert not cad_words.intersection({stage.stage_code, stage.name})


def test_file_delivery_stages_are_correct_and_skip_cad(db: Session):
    _, _, workflow = _make_draft(db, "file_delivery")
    codes = [stage.stage_code for stage in workflow.stages]
    assert codes == ["source_upload", "quality_review", "delivery"]


def test_stage_sequences_are_strictly_increasing_by_one(db: Session):
    _, _, workflow = _make_draft(db)
    seqs = [stage.sequence for stage in workflow.stages]
    assert seqs == list(range(1, len(seqs) + 1))


def test_draft_workflow_initial_state_is_correct(db: Session):
    """Draft has progress=0, status=draft, current_stage=None (not yet started)."""
    _, _, workflow = _make_draft(db)
    assert workflow.progress == 0
    assert workflow.status == "draft"
    assert workflow.current_stage is None
    assert workflow.started_at is None
    assert workflow.finished_at is None


def test_first_stage_is_ready_others_pending(db: Session):
    _, _, workflow = _make_draft(db)
    assert workflow.stages[0].status == "ready"
    for stage in workflow.stages[1:]:
        assert stage.status == "pending"


# ── start boundaries ─────────────────────────────────────────────────────────


def test_cannot_start_non_draft_workflow(db: Session):
    _, _, workflow = _make_draft(db)
    start_workflow(db, workflow)
    with pytest.raises(AppHTTPException, match="Only a draft workflow can start"):
        start_workflow(db, workflow)


def test_cannot_start_terminal_workflow(db: Session):
    _, _, workflow = _make_draft(db)
    start_workflow(db, workflow)
    cancel_workflow(workflow)
    with pytest.raises(AppHTTPException, match="Only a draft workflow can start"):
        start_workflow(db, workflow)


def test_start_transitions_from_draft_to_waiting_input(db: Session):
    _, _, workflow = _make_draft(db)
    assert workflow.status == "draft"
    start_workflow(db, workflow)
    assert workflow.status == "waiting_input"
    assert workflow.current_stage == workflow.stages[0].stage_code
    assert workflow.started_at is not None


# ── manual completion boundaries ─────────────────────────────────────────────


def test_cannot_complete_running_stage(db: Session):
    """A stage bound to a running Job must not be manually completed."""
    _, _, workflow = _make_draft(db)
    start_workflow(db, workflow)
    job = Job(
        task_type="framework_smoke_test",
        precision_level="normal",
        pipeline="local_stub",
        status="running",
        attempt=1,
        progress=42,
    )
    db.add(job)
    db.flush()
    bind_stage_job(db, workflow, stage_code=workflow.stages[0].stage_code, job=job)
    db.commit()
    with pytest.raises(AppHTTPException, match="not awaiting input"):
        complete_manual_stage(workflow, workflow.stages[0].stage_code)


def test_cannot_complete_unknown_stage_code(db: Session):
    _, _, workflow = _make_draft(db)
    start_workflow(db, workflow)
    with pytest.raises(AppHTTPException, match="Unknown workflow stage"):
        complete_manual_stage(workflow, "nonexistent")


def test_cannot_complete_already_succeeded_stage(db: Session):
    _, _, workflow = _make_draft(db)
    start_workflow(db, workflow)
    complete_manual_stage(workflow, workflow.stages[0].stage_code)
    with pytest.raises(AppHTTPException, match="not awaiting input"):
        complete_manual_stage(workflow, workflow.stages[0].stage_code)


def test_complete_manual_stage_advances_to_next(db: Session):
    _, _, workflow = _make_draft(db)
    start_workflow(db, workflow)
    complete_manual_stage(workflow, "source_upload")
    assert workflow.stages[0].status == "succeeded"
    assert workflow.stages[0].progress == 100
    assert workflow.stages[0].finished_at is not None
    assert workflow.stages[1].status == "waiting_input"
    assert workflow.current_stage == workflow.stages[1].stage_code


def test_full_file_delivery_lifecycle(db: Session):
    _, _, workflow = _make_draft(db, "file_delivery")
    start_workflow(db, workflow)
    assert workflow.status == "waiting_input"
    complete_manual_stage(workflow, "source_upload")
    assert workflow.status == "waiting_input"
    assert workflow.current_stage == "quality_review"
    complete_manual_stage(workflow, "quality_review")
    assert workflow.current_stage == "delivery"
    complete_manual_stage(workflow, "delivery")
    assert workflow.status == "succeeded"
    assert workflow.progress == 100
    assert workflow.finished_at is not None


def test_full_excel_delivery_lifecycle(db: Session):
    _, _, workflow = _make_draft(db, "excel_delivery")
    start_workflow(db, workflow)
    assert workflow.status == "waiting_input"
    complete_manual_stage(workflow, "source_upload")
    assert workflow.current_stage == "excel_process"
    complete_manual_stage(workflow, "excel_process")
    assert workflow.current_stage == "quality_review"
    complete_manual_stage(workflow, "quality_review")
    assert workflow.current_stage == "delivery"
    complete_manual_stage(workflow, "delivery")
    assert workflow.status == "succeeded"
    assert workflow.progress == 100


# ── cancellation boundaries ──────────────────────────────────────────────────


def test_cannot_cancel_already_cancelled_workflow(db: Session):
    _, _, workflow = _make_draft(db)
    start_workflow(db, workflow)
    cancel_workflow(workflow)
    assert workflow.status == "cancelled"
    with pytest.raises(AppHTTPException, match="already terminal"):
        cancel_workflow(workflow)


def test_cannot_cancel_succeeded_workflow(db: Session):
    _, _, workflow = _make_draft(db, "file_delivery")
    start_workflow(db, workflow)
    for stage in workflow.stages:
        complete_manual_stage(workflow, stage.stage_code)
    assert workflow.status == "succeeded"
    with pytest.raises(AppHTTPException, match="already terminal"):
        cancel_workflow(workflow)


def test_cancelling_partial_workflow_cancels_remaining_stages(db: Session):
    _, _, workflow = _make_draft(db, "file_delivery")
    start_workflow(db, workflow)
    complete_manual_stage(workflow, "source_upload")
    cancel_workflow(workflow)
    assert workflow.status == "cancelled"
    assert workflow.stages[0].status == "succeeded"
    for stage in workflow.stages[1:]:
        assert stage.status == "cancelled"


def test_cancelling_draft_workflow_cancels_all_stages(db: Session):
    _, _, workflow = _make_draft(db)
    cancel_workflow(workflow)
    assert workflow.status == "cancelled"
    assert all(stage.status == "cancelled" for stage in workflow.stages)


# ── artifact boundaries ──────────────────────────────────────────────────────


def test_artifact_needs_file_or_result(db: Session):
    _, _, workflow = _make_draft(db)
    with pytest.raises(AppHTTPException, match="must reference a file or result"):
        attach_artifact(db, workflow, stage_code="source_upload", artifact_type="input")


def test_artifact_unknown_stage_rejected(db: Session):
    _, _, workflow = _make_draft(db)
    with pytest.raises(AppHTTPException, match="Unknown workflow stage"):
        attach_artifact(
            db, workflow, stage_code="ghost_stage", artifact_type="output", file_id=1
        )


def test_artifact_attached_with_valid_file_id(db: Session):
    _, _, workflow = _make_draft(db)
    stored = _seed_file(db)
    artifact = attach_artifact(
        db, workflow, stage_code="source_upload", artifact_type="source_file", file_id=stored.id
    )
    db.commit()
    assert artifact.file_id == stored.id
    assert artifact.result_id is None
    assert artifact.version == 1
    assert artifact.artifact_type == "source_file"
    assert artifact.stage_run_id == workflow.stages[0].id


def test_artifact_only_file_id_accepted(db: Session):
    """Providing only file_id (no result_id) is valid per the service guard."""
    _, _, workflow = _make_draft(db)
    stored = _seed_file(db)
    artifact = attach_artifact(
        db, workflow, stage_code="source_upload", artifact_type="input", file_id=stored.id
    )
    db.commit()
    assert artifact.file_id == stored.id
    assert artifact.result_id is None


def test_artifact_with_metadata_preserved(db: Session):
    _, _, workflow = _make_draft(db)
    stored = _seed_file(db)
    metadata = {"version": "2.1", "source": "upload"}
    artifact = attach_artifact(
        db,
        workflow,
        stage_code="source_upload",
        artifact_type="input",
        file_id=stored.id,
        metadata=metadata,
    )
    db.commit()
    assert artifact.metadata_json == metadata


def test_multiple_artifacts_share_same_stage(db: Session):
    _, _, workflow = _make_draft(db)
    f1 = _seed_file(db)
    f2 = _seed_file(db)
    a1 = attach_artifact(
        db, workflow, stage_code="source_upload", artifact_type="input", file_id=f1.id
    )
    a2 = attach_artifact(
        db, workflow, stage_code="source_upload", artifact_type="output", file_id=f2.id
    )
    db.commit()
    assert a1.stage_run_id == workflow.stages[0].id
    assert a2.stage_run_id == workflow.stages[0].id
    assert len(workflow.artifacts) == 2


# ── bind_stage_job boundaries ────────────────────────────────────────────────


def test_bind_job_to_unknown_stage_rejected(db: Session):
    _, _, workflow = _make_draft(db)
    job = Job(
        task_type="process_excel_final",
        precision_level="normal",
        pipeline="excel_final",
        status="queued",
        attempt=1,
        progress=0,
    )
    db.add(job)
    db.flush()
    with pytest.raises(AppHTTPException, match="Unknown workflow stage"):
        bind_stage_job(db, workflow, stage_code="ghost_stage", job=job)


def test_bind_job_sets_stage_and_workflow_status(db: Session):
    _, _, workflow = _make_draft(db)
    start_workflow(db, workflow)
    job = Job(
        task_type="process_excel_final",
        precision_level="normal",
        pipeline="excel_final",
        status="queued",
        attempt=2,
        progress=10,
    )
    db.add(job)
    db.flush()
    bind_stage_job(db, workflow, stage_code="source_upload", job=job)
    db.commit()
    stage = workflow.stages[0]
    assert stage.job_id == job.id
    assert stage.job_attempt == 2
    assert stage.status == "queued"
    assert workflow.status == "running"


def test_bind_job_to_terminal_workflow_rejected(db: Session):
    _, _, workflow = _make_draft(db, "file_delivery")
    start_workflow(db, workflow)
    for stage in workflow.stages:
        complete_manual_stage(workflow, stage.stage_code)
    assert workflow.status == "succeeded"
    job = Job(
        task_type="framework_smoke_test",
        precision_level="normal",
        pipeline="local_stub",
        status="queued",
        attempt=1,
        progress=0,
    )
    db.add(job)
    db.flush()
    with pytest.raises(AppHTTPException, match="Terminal workflow cannot accept a job"):
        bind_stage_job(db, workflow, stage_code="delivery", job=job)


# ── sync_workflow_from_jobs boundaries ───────────────────────────────────────


def test_sync_does_nothing_for_unbound_stages(db: Session):
    _, _, workflow = _make_draft(db, "file_delivery")
    start_workflow(db, workflow)
    result = sync_workflow_from_jobs(db, workflow)
    assert result.status == "waiting_input"


def test_sync_updates_from_completed_job(db: Session):
    _, _, workflow = _make_draft(db)
    start_workflow(db, workflow)
    job = Job(
        task_type="process_excel_final",
        precision_level="normal",
        pipeline="excel_final",
        status="queued",
        attempt=1,
        progress=0,
    )
    db.add(job)
    db.flush()
    bind_stage_job(db, workflow, stage_code="source_upload", job=job)
    db.commit()
    job.status = "succeeded"
    job.progress = 100
    result = sync_workflow_from_jobs(db, workflow)
    stage = result.stages[0]
    assert stage.status == "succeeded"
    assert stage.progress == 100


def test_sync_preserves_failure_from_job(db: Session):
    _, _, workflow = _make_draft(db)
    start_workflow(db, workflow)
    job = Job(
        task_type="process_excel_final",
        precision_level="normal",
        pipeline="excel_final",
        status="queued",
        attempt=1,
        progress=0,
    )
    db.add(job)
    db.flush()
    bind_stage_job(db, workflow, stage_code="source_upload", job=job)
    db.commit()
    job.status = "failed"
    job.progress = 30
    job.error_code = "PROCESS_CRASH"
    job.error_message = "Child process timed out"
    result = sync_workflow_from_jobs(db, workflow)
    assert result.status == "failed"
    assert result.error_code == "PROCESS_CRASH"
    assert "timed out" in (result.error_message or "")


def test_sync_ignores_job_with_mismatched_attempt(db: Session):
    """Only the recorded attempt matters — a new attempt on the same job is ignored."""
    _, _, workflow = _make_draft(db)
    start_workflow(db, workflow)
    job = Job(
        task_type="process_excel_final",
        precision_level="normal",
        pipeline="excel_final",
        status="queued",
        attempt=2,
        progress=20,
    )
    db.add(job)
    db.flush()
    bind_stage_job(db, workflow, stage_code="source_upload", job=job)
    db.commit()
    job.attempt = 3
    job.status = "succeeded"
    db.flush()
    result = sync_workflow_from_jobs(db, workflow)
    assert result.stages[0].status == "queued"


def test_sync_job_still_running_keeps_workflow_running(db: Session):
    _, _, workflow = _make_draft(db)
    start_workflow(db, workflow)
    job = Job(
        task_type="process_excel_final",
        precision_level="normal",
        pipeline="excel_final",
        status="queued",
        attempt=1,
        progress=40,
    )
    db.add(job)
    db.flush()
    bind_stage_job(db, workflow, stage_code="source_upload", job=job)
    db.commit()
    job.status = "running"
    sync_workflow_from_jobs(db, workflow)
    assert workflow.status == "running"


# ── recompute boundaries ─────────────────────────────────────────────────────


def test_recompute_progress_averages_stages(db: Session):
    _, _, workflow = _make_draft(db, "file_delivery")
    start_workflow(db, workflow)
    workflow.stages[0].progress = 100
    workflow.stages[0].status = "succeeded"
    workflow.stages[1].progress = 50
    workflow.stages[1].status = "waiting_review"
    workflow.stages[2].progress = 0
    recompute_workflow(workflow)
    assert round(workflow.progress) == round((100 + 50 + 0) / 3)


def test_recompute_detects_first_failed_stage(db: Session):
    _, _, workflow = _make_draft(db, "file_delivery")
    start_workflow(db, workflow)
    workflow.stages[0].status = "succeeded"
    workflow.stages[1].status = "failed"
    workflow.stages[1].error_code = "E1"
    workflow.stages[1].error_message = "bad"
    recompute_workflow(workflow)
    assert workflow.status == "failed"
    assert workflow.current_stage == workflow.stages[1].stage_code
    assert workflow.error_code == "E1"


def test_recompute_all_succeeded_marks_terminal(db: Session):
    _, _, workflow = _make_draft(db, "file_delivery")
    start_workflow(db, workflow)
    for stage in workflow.stages:
        stage.status = "succeeded"
        stage.progress = 100
    recompute_workflow(workflow)
    assert workflow.status == "succeeded"
    assert workflow.progress == 100
    assert workflow.finished_at is not None


def test_recompute_current_stage_is_first_non_terminal(db: Session):
    _, _, workflow = _make_draft(db, "file_delivery")
    start_workflow(db, workflow)
    workflow.stages[0].status = "succeeded"
    workflow.stages[1].status = "running"
    recompute_workflow(workflow)
    assert workflow.current_stage == workflow.stages[1].stage_code
    assert workflow.status == "running"


def test_recompute_waiting_review_recognized(db: Session):
    _, _, workflow = _make_draft(db, "file_delivery")
    start_workflow(db, workflow)
    workflow.stages[0].status = "succeeded"
    workflow.stages[1].status = "waiting_review"
    recompute_workflow(workflow)
    assert workflow.status == "waiting_review"


# ── service constructor edges ────────────────────────────────────────────────


def test_config_json_preserved(db: Session):
    user, project = _owner_project(db)
    config = {"threshold": 0.8, "auto_review": False}
    workflow = create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id, name="Config Test", workflow_type="excel_delivery", config=config
        ),
        created_by=user.id,
    )
    assert workflow.config_json == config


def test_long_name_accepted(db: Session):
    user, project = _owner_project(db)
    name = "X" * 128
    workflow = create_workflow(
        db,
        WorkflowCreate(project_id=project.id, name=name, workflow_type="file_delivery"),
        created_by=user.id,
    )
    assert workflow.name == name


def test_blank_name_after_trim_is_rejected(db: Session):
    user, project = _owner_project(db)
    with pytest.raises((ValueError, AppHTTPException)):
        create_workflow(
            db,
            WorkflowCreate(project_id=project.id, name="   ", workflow_type="file_delivery"),
            created_by=user.id,
        )


# ── concurrent / retry-style guards ──────────────────────────────────────────


def test_recompute_handles_empty_stage_list(db: Session):
    """An edge-case: if stages were somehow cleared, recompute must not crash."""
    user, project = _owner_project(db)
    workflow = create_workflow(
        db,
        WorkflowCreate(project_id=project.id, name="Empty stages", workflow_type="file_delivery"),
        created_by=user.id,
    )
    workflow.stages.clear()
    recompute_workflow(workflow)
    assert workflow.progress == 0
