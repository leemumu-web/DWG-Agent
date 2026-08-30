"""Steel DXF 分类对外的公共边界。

调用契约（CONTEXT.md：Interface 必须文档化不变量、错误模式、顺序与配置）：

- ``run_dxf_classification(job_id)`` 是 worker 侧执行入口：经适配器调用
  Steel DXF Classifier CLI，强制不可变源不变式（分类输出必须与冻结输入
  DXF 逐字节一致），并在分类账本记录 run/items。先投递
  （``enqueue_dxf_classification_job``），再执行。
- 批次交接：``load_bh_stage2_classification_batch`` /
  ``load_box_stage2_classification_batch`` 从分类账本返回不可变的 stage-2
  输入批次；当 ``expected_run_id`` 与当前运行不匹配或运行未完成时抛
  ``ClassificationError``。返回的 manifest 携带 ``bh_manifest_version`` 与
  规范化清单行的 SHA-256——消费方使用前必须校验，且不得改动批次。
- 对账：``reconcile_dxf_classification_run_for_terminal_job`` 只为精确的
  (job_id, attempt) 世代关闭投影；``reconcile_orphan_dxf_classification_runs``
  关闭 Job 已不再活跃的投影。两者都是读修复边界，不是 Job 写入方。
- 版本：``CLASSIFIER_VERSION`` 是写入每次运行的权威 Stage 版本；模型默认值
  与 README 必须与之保持同步。
"""

from app.modules.dxf_classification.adapter import (
    CLASSIFIER_VERSION,
    CLI_SCHEMA,
    REPORT_SCHEMA,
    ClassificationError,
    classifier_project_name,
)
from app.modules.dxf_classification.models import DxfClassificationItem, DxfClassificationRun
from app.modules.dxf_classification.presentation import (
    build_classification_group_page,
    build_classification_run_read,
)
from app.modules.dxf_classification.schemas import (
    DxfBhStage2ClassificationBatch,
    DxfBhStage2Input,
    DxfClassificationGroupItemRead,
    DxfClassificationGroupPage,
    DxfClassificationGroupRead,
    DxfClassificationItemRead,
    DxfClassificationRunRead,
    DxfNextStageInput,
    DxfSplitCandidateInput,
)


def run_dxf_classification(job_id: int, **kwargs) -> None:
    """执行一个分类 Job（worker 侧，按 status+attempt 守卫）。"""
    from app.modules.dxf_classification.execution import run_dxf_classification as run

    run(job_id, **kwargs)


def latest_classification_run(db, workflow_id: int) -> DxfClassificationRun | None:
    """返回某工作流最近一次分类运行（如有）。"""
    from app.modules.dxf_classification.persistence import latest_classification_run as latest

    return latest(db, workflow_id)


def list_next_stage_inputs(db, workflow_id: int) -> list[DxfNextStageInput]:
    """列出某工作流路由到下一阶段的冻结 DXF 输入。"""
    from app.modules.dxf_classification.persistence import (
        list_next_stage_inputs as list_inputs,
    )

    return list_inputs(db, workflow_id)


def list_split_candidate_inputs(db, workflow_id: int) -> list[DxfSplitCandidateInput]:
    """列出某工作流路由到拆板阶段的冻结 DXF 输入。"""
    from app.modules.dxf_classification.persistence import (
        list_split_candidate_inputs as list_inputs,
    )

    return list_inputs(db, workflow_id)


def list_pl_split_candidate_inputs(db, workflow_id: int) -> list[DxfSplitCandidateInput]:
    """列出只允许进入独立 PL Stage 的冻结 PL DXF。"""
    from app.modules.dxf_classification.persistence import (
        list_pl_split_candidate_inputs as list_inputs,
    )

    return list_inputs(db, workflow_id)


def load_bh_stage2_classification_batch(
    db,
    workflow_id: int,
    *,
    expected_run_id: int | None = None,
) -> DxfBhStage2ClassificationBatch:
    """加载某工作流的不可变 BH stage-2 输入批次。

    当前运行 id 与 ``expected_run_id`` 不匹配或运行未完成时抛
    ClassificationError；使用前必须用 SHA-256 校验批次 manifest。
    """
    from app.modules.dxf_classification.persistence import (
        load_bh_stage2_classification_batch as load_batch,
    )

    return load_batch(db, workflow_id, expected_run_id=expected_run_id)


def load_box_stage2_classification_batch(
    db,
    workflow_id: int,
    *,
    expected_run_id: int | None = None,
) -> DxfBhStage2ClassificationBatch:
    """加载某工作流的不可变 BOX stage-2 输入批次。

    契约同 ``load_bh_stage2_classification_batch``；使用前用 SHA-256 校验
    manifest。
    """
    from app.modules.dxf_classification.persistence import (
        load_box_stage2_classification_batch as load_batch,
    )

    return load_batch(db, workflow_id, expected_run_id=expected_run_id)


def enqueue_dxf_classification_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递一个分类 Job 到 Celery；返回 task id。"""
    from app.modules.dxf_classification.tasks import classify_steel_dxf_task

    return str(
        classify_steel_dxf_task.apply_async(
            args=[job_id, attempt], task_id=task_id
        ).id
    )


def reconcile_dxf_classification_run_for_terminal_job(
    db, *, job_id: int, attempt: int
) -> bool:
    """为精确的单个 Job attempt 关闭分类投影。"""
    from app.modules.dxf_classification.persistence import (
        reconcile_classification_run_for_terminal_job,
    )

    return reconcile_classification_run_for_terminal_job(
        db, job_id=job_id, attempt=attempt
    )


def reconcile_orphan_dxf_classification_runs(db) -> int:
    """关闭 Job 已不再活跃的分类投影。

    读修复边界；返回关闭的运行数。
    """
    from app.modules.dxf_classification.persistence import (
        reconcile_orphan_classification_runs,
    )

    return reconcile_orphan_classification_runs(db)


__all__ = [
    "CLASSIFIER_VERSION",
    "CLI_SCHEMA",
    "ClassificationError",
    "REPORT_SCHEMA",
    "DxfClassificationItem",
    "DxfBhStage2ClassificationBatch",
    "DxfBhStage2Input",
    "DxfClassificationItemRead",
    "DxfClassificationGroupItemRead",
    "DxfClassificationGroupPage",
    "DxfClassificationGroupRead",
    "DxfNextStageInput",
    "DxfSplitCandidateInput",
    "DxfClassificationRun",
    "DxfClassificationRunRead",
    "build_classification_group_page",
    "build_classification_run_read",
    "classifier_project_name",
    "enqueue_dxf_classification_job",
    "latest_classification_run",
    "load_bh_stage2_classification_batch",
    "list_next_stage_inputs",
    "list_pl_split_candidate_inputs",
    "list_split_candidate_inputs",
    "run_dxf_classification",
]
