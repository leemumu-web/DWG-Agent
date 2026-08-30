"""DXF 拆板对工作流/HTTP/Excel 处理的公共边界。

调用契约（CONTEXT.md：Interface 必须文档化不变量、错误模式、顺序与配置）：

- 执行：``run_dxf_splitting(job_id)`` 是 worker 侧入口，调用 Steel DXF
  Split CLI（退出码 0/1/2/3 对应 全部自动接纳 / 有待复核 / 有失败 /
  批次级失败）并记录运行账本。``MAX_AUTOMATIC_ATTEMPTS = 1``——失败的
  自动 attempt 不会自动重试；恢复走人工复核或新 attempt。
- 定向导出：``create_download_token`` 签发绑定 workflow/run/export_uid
  及其类别清单的 JWT，有效期 ``workflow_batch_export_ttl_minutes``；
  ``require_download_token`` 过期抛 410、无效/伪造抛 403。导出流式传输
  成员文件，不做服务器端 ZIP 暂存（``storage_members`` +
  ``export_preview``）。
- 复核：``list_split_review_items`` / ``decide_split_item`` /
  ``complete_split_review`` 驱动人工复核状态机；归档成员助手
  （``manual_review_archive_members`` / ``review_candidate_archive_members``
  / ``split_results_archive_members``）枚举各结果类别的 file id。
- Excel 交接：``get_excel_split_handoff`` 要求工作流当前 attempt 存在正式
  拆板运行；其 ``mode`` 仅在 drawing_processing 阶段以
  ``reason = "no_split_candidates"`` 跳过时为 ``no_split_candidates``——
  修改这个隐藏字符串契约的任何一侧都必须同步另一侧。
- 对账与分类侧一致：只为精确的 (job_id, attempt) 或孤儿运行关闭投影。
"""

from app.modules.dxf_splitting.adapter import (
    BH_SOURCE_CONTRACT,
    BOX_SOURCE_CONTRACT,
    CLI_SCHEMA,
    MANIFEST_SCHEMA,
    MAX_AUTOMATIC_ATTEMPTS,
    SPLITTER_VERSION,
    VALIDATION_SCHEMA,
    DxfSplitError,
)
from app.modules.dxf_splitting.models import (
    DxfSplitItem,
    DxfSplitReviewDecision,
    DxfSplitRun,
)
from app.modules.dxf_splitting.pl_selective_exports import (
    PL_SELECTIVE_EXPORT_COOKIE_NAME,
    create_pl_download_token,
    pl_export_download_path,
    pl_export_filename,
    pl_export_preview,
    pl_storage_members,
    require_pl_download_token,
)
from app.modules.dxf_splitting.presentation import (
    build_dxf_split_run_read,
    build_pl_split_run_read,
)
from app.modules.dxf_splitting.schemas import (
    DxfSplitExcelHandoff,
    DxfSplitHandoffDrawing,
    DxfSplitItemRead,
    DxfSplitReviewDecisionRead,
    DxfSplitReviewDecisionWrite,
    DxfSplitReviewPage,
    DxfSplitRunRead,
    PlSplitRunRead,
)
from app.modules.dxf_splitting.selective_exports import (
    SELECTIVE_EXPORT_COOKIE_NAME,
    create_download_token,
    export_download_path,
    export_filename,
    export_preview,
    require_download_token,
    storage_members,
)


def list_split_review_items(db, **kwargs) -> DxfSplitReviewPage:
    """分页列出拆板运行的人工复核候选。"""
    from app.modules.dxf_splitting.review import list_split_review_items as list_items

    return list_items(db, **kwargs)


def decide_split_item(db, **kwargs) -> DxfSplitReviewDecision:
    """为一条复核候选条目记录人工决定。"""
    from app.modules.dxf_splitting.review import decide_split_item as decide

    return decide(db, **kwargs)


def complete_split_review(db, **kwargs) -> DxfSplitRun:
    """结束复核轮次并固化运行的正式结果。"""
    from app.modules.dxf_splitting.review import complete_split_review as complete

    return complete(db, **kwargs)


def run_dxf_splitting(job_id: int, **kwargs) -> None:
    """执行一个拆板 Job（worker 侧，按 status+attempt 守卫）。"""
    from app.modules.dxf_splitting.execution import run_dxf_splitting as run

    run(job_id, **kwargs)


def run_pl_dxf_splitting(job_id: int, **kwargs) -> None:
    """执行一个独立 PL 拆板 Job（worker 侧，按 status+attempt 守卫）。"""
    from app.modules.dxf_splitting.pl_execution import run_pl_dxf_splitting as run

    run(job_id, **kwargs)


def pl_dxf_split_run_for_job(db, *, job_id: int, attempt: int) -> DxfSplitRun | None:
    """返回精确 PL 阶段 Job attempt 的拆板运行。"""
    from app.modules.dxf_splitting.persistence import split_run_for_job

    return split_run_for_job(db, job_id=job_id, attempt=attempt)


def enqueue_dxf_splitting_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递一个拆板 Job 到 Celery；返回 task id。"""
    from app.modules.dxf_splitting.tasks import split_steel_dxf_task

    return str(
        split_steel_dxf_task.apply_async(
            args=[job_id, attempt], task_id=task_id
        ).id
    )


def enqueue_pl_dxf_splitting_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递独立 PL 拆板 Job 到 Celery。"""
    from app.modules.dxf_splitting.tasks import split_pl_dxf_task

    return str(
        split_pl_dxf_task.apply_async(
            args=[job_id, attempt], task_id=task_id
        ).id
    )


def latest_dxf_split_run(db, workflow_id: int) -> DxfSplitRun | None:
    """返回某工作流最近一次拆板运行（如有）。"""
    from app.modules.dxf_splitting.persistence import latest_split_run

    return latest_split_run(db, workflow_id)


def get_dxf_split_outcome(db, *, job_id: int, attempt: int) -> str | None:
    """返回精确 (job_id, attempt) 的正式结果（如有）。"""
    from app.modules.dxf_splitting.persistence import get_split_outcome

    return get_split_outcome(db, job_id=job_id, attempt=attempt)


def reconcile_dxf_split_run_for_terminal_job(
    db,
    *,
    job_id: int,
    attempt: int,
) -> bool:
    """为精确的单个 Job attempt 关闭拆板投影。"""
    from app.modules.dxf_splitting.persistence import (
        reconcile_split_run_for_terminal_job as reconcile,
    )

    return reconcile(db, job_id=job_id, attempt=attempt)


def reconcile_orphan_dxf_split_runs(db) -> int:
    """关闭 Job 已不再活跃的拆板投影（读修复）。"""
    from app.modules.dxf_splitting.persistence import reconcile_orphan_split_runs

    return reconcile_orphan_split_runs(db)


def manual_review_archive_members(db, run: DxfSplitRun) -> list[tuple[int, str]]:
    """列出归档在人工复核结果类别下的 (file_id, label) 成员。"""
    from app.modules.dxf_splitting.persistence import manual_review_archive_members as members

    return members(db, run)


def review_candidate_archive_members(db, run: DxfSplitRun) -> list[tuple[int, str]]:
    """列出复核候选归档的 (file_id, label) 成员。"""
    from app.modules.dxf_splitting.persistence import (
        review_candidate_archive_members as members,
    )

    return members(db, run)


def split_results_archive_members(db, run: DxfSplitRun) -> list[tuple[int, str]]:
    """列出正式拆板结果归档的 (file_id, label) 成员。"""
    from app.modules.dxf_splitting.persistence import (
        split_results_archive_members as members,
    )

    return members(db, run)


def split_candidate_available(db, item: DxfSplitItem) -> bool:
    """该条目是否仍有待复核的候选文件。"""
    from app.modules.dxf_splitting.persistence import split_candidate_files

    return split_candidate_files(db, item) is not None


def get_excel_split_handoff(db, workflow_id: int) -> DxfSplitExcelHandoff:
    """从工作流拆板账本构建 Excel 处理交接。

    要求工作流当前 attempt 存在正式拆板运行；``no_split_candidates`` 模式
    依赖 drawing_processing 的跳过原因契约（见模块 docstring）。
    """
    from app.modules.dxf_splitting.persistence import get_excel_split_handoff as handoff

    return handoff(db, workflow_id)


def find_dxf_split_file_workflow_id(db, file_id: int) -> int | None:
    """解析拥有某拆板结果文件的工作流（删除守卫）。"""
    from app.modules.dxf_splitting.persistence import (
        find_split_file_workflow_id as find_workflow_id,
    )

    return find_workflow_id(db, file_id)


def dxf_split_file_reference_exists(file_id):
    """是否有拆板账本行仍引用该文件（删除守卫）。"""
    from app.modules.dxf_splitting.persistence import split_file_reference_exists

    return split_file_reference_exists(file_id)


__all__ = [
    "BH_SOURCE_CONTRACT",
    "BOX_SOURCE_CONTRACT",
    "CLI_SCHEMA",
    "MANIFEST_SCHEMA",
    "MAX_AUTOMATIC_ATTEMPTS",
    "PL_SELECTIVE_EXPORT_COOKIE_NAME",
    "SELECTIVE_EXPORT_COOKIE_NAME",
    "SPLITTER_VERSION",
    "VALIDATION_SCHEMA",
    "DxfSplitError",
    "DxfSplitExcelHandoff",
    "DxfSplitHandoffDrawing",
    "DxfSplitItem",
    "DxfSplitItemRead",
    "DxfSplitReviewDecision",
    "DxfSplitReviewDecisionRead",
    "DxfSplitReviewDecisionWrite",
    "DxfSplitReviewPage",
    "DxfSplitRun",
    "DxfSplitRunRead",
    "PlSplitRunRead",
    "build_dxf_split_run_read",
    "build_pl_split_run_read",
    "complete_split_review",
    "create_pl_download_token",
    "create_download_token",
    "decide_split_item",
    "enqueue_dxf_splitting_job",
    "enqueue_pl_dxf_splitting_job",
    "export_download_path",
    "export_filename",
    "export_preview",
    "find_dxf_split_file_workflow_id",
    "dxf_split_file_reference_exists",
    "get_dxf_split_outcome",
    "get_excel_split_handoff",
    "latest_dxf_split_run",
    "list_split_review_items",
    "manual_review_archive_members",
    "pl_export_download_path",
    "pl_export_filename",
    "pl_export_preview",
    "pl_storage_members",
    "reconcile_dxf_split_run_for_terminal_job",
    "reconcile_orphan_dxf_split_runs",
    "require_download_token",
    "require_pl_download_token",
    "review_candidate_archive_members",
    "split_results_archive_members",
    "split_candidate_available",
    "storage_members",
    "run_dxf_splitting",
    "run_pl_dxf_splitting",
    "pl_dxf_split_run_for_job",
]
