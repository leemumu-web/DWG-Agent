"""工作流对其他业务模块的公共边界。

调用契约（CONTEXT.md：Interface 必须文档化不变量、错误模式、顺序与配置）：

- ``bind_stage_job`` 把 Job 挂到阶段并把运行置为 ``running``；只能在输入
  批次**已冻结**后、且只对运行模板声明的阶段调用。终态运行拒绝绑定（409），
  未知阶段抛 422。
- ``sync_workflow_from_jobs`` 是只读投影重放：先调 ``workflow_needs_sync``，
  检测到漂移才同步。绑定的 ``job.attempt`` 与阶段世代不一致时跳过该阶段；
  只接受 ``result_json["job_attempt"]`` 等于当前 attempt 的 succeeded
  Result——旧世代数据绝不进入投影。
- ``find_frozen_input_reference`` / ``read_verified_input_object`` /
  ``production_file_reference_exists`` 支撑文件删除守卫：只暴露冻结清单的
  不可变标识，因此仍被冻结批次引用的文件不可能被物理删除。
- ``attach_artifact`` 按阶段 capability 白名单校验 ``artifact_type``
  （422 ``WORKFLOW_ARTIFACT_TYPE_INVALID``）并执行产物引用规则后再登记。
- ``create_workflow``/``start_workflow``/``cancel_workflow``/
  ``complete_manual_stage``/``recompute_workflow`` 是生命周期入口；
  ``recompute_workflow`` 从不提交——事务边界由调用方持有。
"""

from app.modules.workflows.access import (
    find_production_file_workflow_id,
    production_file_reference_exists,
)
from app.modules.workflows.artifacts import attach_artifact
from app.modules.workflows.intake.registration import (
    FrozenInputReference,
    find_frozen_input_reference,
    read_verified_input_object,
)
from app.modules.workflows.job_sync import bind_stage_job, sync_workflow_from_jobs
from app.modules.workflows.lifecycle import (
    cancel_workflow,
    complete_manual_stage,
    create_workflow,
    get_workflow_or_404,
    recompute_workflow,
    start_workflow,
)
from app.modules.workflows.models import (
    WorkflowArtifact,
    WorkflowBatchExport,
    WorkflowInputBatch,
    WorkflowInputItem,
    WorkflowRetentionExport,
    WorkflowRun,
    WorkflowStageRun,
)
from app.modules.workflows.templates import list_workflow_templates

__all__ = [
    "FrozenInputReference",
    "WorkflowArtifact",
    "WorkflowBatchExport",
    "WorkflowInputBatch",
    "WorkflowInputItem",
    "WorkflowRetentionExport",
    "WorkflowRun",
    "WorkflowStageRun",
    "attach_artifact",
    "bind_stage_job",
    "cancel_workflow",
    "complete_manual_stage",
    "create_workflow",
    "find_frozen_input_reference",
    "find_production_file_workflow_id",
    "production_file_reference_exists",
    "get_workflow_or_404",
    "list_workflow_templates",
    "read_verified_input_object",
    "recompute_workflow",
    "start_workflow",
    "sync_workflow_from_jobs",
]
