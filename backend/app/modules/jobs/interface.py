"""Job/Result/Review 对其他业务模块的公共边界。

调用契约（CONTEXT.md：Interface 必须文档化调用方必须知道的不变量、
错误模式、顺序和配置，而不只是函数签名）：

- 投递暂存：``stage_job_dispatch`` / ``stage_conversion_dispatch`` 只在
  调用方事务内写入 outbox 行。它们必须在创建 Job 的**同一事务内、commit
  之前**调用；随后 Celery worker 在它自己的短提交事务中租约并发布整组。
  ``drain_eager_dispatches`` 仅用于 Celery eager 运行时。
- worker 侧写入：worker 唯一的写入口是 ``claim_queued_job`` /
  ``commit_job_progress`` / ``complete_job_attempt`` / ``fail_job_attempt``。
  每次更新都以 ``status`` **和**精确的 ``attempt`` 双重守卫（fencing）：
  旧 attempt 的过期消息或 worker 无法覆盖当前世代的运行状态。
- ``run_local_stub_job`` 是非生产 stub 执行器，仅用于测试与本地开发；
  绝不能当作真实 worker 路径使用。
- ``reconcile_stale_running_jobs`` 是 worker 未结算就死亡时的恢复边界，
  不是 broker 租约；它由维护队列定时执行，只处理超过 ``timeout_seconds``
  的陈旧行。
- 读取助手（``require_*`` / ``job_read_filter``）强制项目级 RBAC；拒绝时
  抛 403/404 AppHTTPException，所有跨模块的 Job/Result 读取必须经过它们。
"""

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
    """在当前进程内同步执行一个排队中的 Job。

    非生产路径：以给定 attempt 认领 Job，执行本地 stub 并结算该 attempt
    （成功则完成，失败则置为失败）。认领未命中（已被认领 / attempt 已推进
    / 已终态）属于预期竞态，静默返回。
    """
    from app.modules.jobs.stub_execution import run_local_stub_job as run_stub

    run_stub(job_id, worker_name=worker_name, expected_attempt=expected_attempt)


def summarize_job_execution(
    job_id: int,
    pipeline: str,
    *,
    session_factory: sessionmaker | None = None,
) -> dict[str, int | str]:
    """汇总某条管线在当前 attempt 下已执行的步骤。

    只读；供控制面/状态视图渲染步骤进度，不修改任何 Job 状态。
    """
    from app.modules.jobs.recovery import summarize_job_execution as summarize

    return summarize(job_id, pipeline, session_factory=session_factory)


def reconcile_stale_running_jobs(
    session_factory: sessionmaker = SessionLocal,
    *,
    timeout_seconds: int | None = None,
) -> int:
    """失败化因 worker 死亡而遗留的运行中 Job（恢复边界）。

    由维护队列定时执行，不是 broker 租约：匹配超过陈旧窗口的
    running/queued 行并置为 failed，以便重试开启新 attempt。
    """
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
