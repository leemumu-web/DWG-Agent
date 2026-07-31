"""Public Job/Result/Review boundary for other business modules."""

from sqlalchemy.orm import sessionmaker

from app.modules.jobs.access import (
    PROJECT_JOB_WRITE_ROLES,
    PROJECT_REVIEW_ROLES,
    get_result_job,
    job_read_filter,
    require_job_read_access,
    require_job_write_access,
    require_result_read_access,
    require_result_review_access,
)
from app.modules.jobs.creation import (
    create_conversion_jobs,
    create_job,
    create_or_reuse_job,
)
from app.modules.jobs.dispatch import (
    dispatch_committed_conversion_batch,
    dispatch_committed_job,
)
from app.modules.jobs.event_stream import (
    job_event_from_row,
    job_event_stream,
    jobs_event_stream,
    make_event,
    publish_job_event,
)
from app.modules.jobs.lifecycle import (
    cancel_job,
    claim_queued_job,
    commit_job_progress,
    complete_job_attempt,
    fail_job_attempt,
    rerun_succeeded_job,
    retry_job,
)
from app.modules.jobs.models import AnalysisResult, Job, JobDispatch, JobStep, ReviewRecord
from app.modules.jobs.outbox import (
    drain_eager_dispatches,
    stage_conversion_dispatch,
    stage_job_dispatch,
)
from app.modules.jobs.reviews import create_review
from app.modules.jobs.schemas import (
    AnalysisResultRead,
    ConversionBatchCreate,
    JobBulkCancellation,
    JobCreate,
    JobRead,
    JobStepRead,
    ReviewCreate,
    ReviewRead,
)
from app.platform.database.session import SessionLocal


def run_local_stub_job(
    job_id: int,
    worker_name: str = "celery_stub",
    expected_attempt: int = 1,
) -> None:
    from app.modules.jobs.stub_execution import run_local_stub_job as run_stub

    run_stub(job_id, worker_name=worker_name, expected_attempt=expected_attempt)


def summarize_job_execution(
    job_id: int,
    pipeline: str,
    *,
    session_factory: sessionmaker | None = None,
) -> dict[str, int | str]:
    from app.modules.jobs.recovery import summarize_job_execution as summarize

    return summarize(job_id, pipeline, session_factory=session_factory)


def reconcile_stale_running_jobs(
    session_factory: sessionmaker = SessionLocal,
    *,
    timeout_seconds: int | None = None,
) -> int:
    from app.modules.jobs.recovery import reconcile_stale_running_jobs as reconcile

    return reconcile(session_factory, timeout_seconds=timeout_seconds)


__all__ = [
    "AnalysisResult",
    "AnalysisResultRead",
    "ConversionBatchCreate",
    "Job",
    "JobBulkCancellation",
    "JobCreate",
    "JobDispatch",
    "JobRead",
    "JobStep",
    "JobStepRead",
    "PROJECT_JOB_WRITE_ROLES",
    "PROJECT_REVIEW_ROLES",
    "ReviewCreate",
    "ReviewRead",
    "ReviewRecord",
    "cancel_job",
    "claim_queued_job",
    "commit_job_progress",
    "complete_job_attempt",
    "create_conversion_jobs",
    "create_job",
    "create_or_reuse_job",
    "create_review",
    "drain_eager_dispatches",
    "dispatch_committed_conversion_batch",
    "dispatch_committed_job",
    "fail_job_attempt",
    "get_result_job",
    "job_event_from_row",
    "job_event_stream",
    "job_read_filter",
    "jobs_event_stream",
    "make_event",
    "publish_job_event",
    "reconcile_stale_running_jobs",
    "require_job_read_access",
    "require_job_write_access",
    "require_result_read_access",
    "require_result_review_access",
    "retry_job",
    "rerun_succeeded_job",
    "run_local_stub_job",
    "summarize_job_execution",
    "stage_conversion_dispatch",
    "stage_job_dispatch",
]
