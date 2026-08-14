"""CAD 处理对文件/Job/工作流模块的公共边界。

调用契约（CONTEXT.md：Interface 必须文档化不变量、错误模式、顺序与配置）：

- 执行与投递分离：``run_*_conversion(job_id)`` 与 ``run_*_batch`` 是
  worker 侧执行入口——在 Celery 任务内按 status+attempt 认领 Job。
  ``enqueue_*`` 是投递侧入口（``apply_async``）；先投递，再由 worker
  执行。批量函数接收 ``(job_id, attempt)`` 成对参数；不要传裸 job id。
- ``convert_dwg_directory`` 是余料域的目录级单次 ODA 调用适配器；它不
  拥有账本，失败的图纸会从结果中静默省略。
- 预览：``get_or_create_dxf_preview`` 对大小/复杂度/状态违规抛
  ``AppHTTPException``（409/413/422）；渲染前先调
  ``validate_dxf_source_size`` 并遵守 ``MAX_DXF_SIZE_BYTES``。预览产物按
  源哈希缓存；源变化时用 ``invalidate_dxf_previews_for_source`` 失效。
"""

from app.modules.cad_processing.preview import MAX_DXF_SIZE_BYTES


def convert_dwg_directory(inputs, output_dir):
    """一次 ODA 调用转换一个目录下的 DWG 输入（余料域）。

    余料管线的目录级适配器：只返回成功项；失败的图纸被静默省略——调用方
    必须自行暴露部分失败。
    """
    from app.modules.cad_processing.remnant_conversion import convert_dwg_directory as convert

    return convert(inputs, output_dir)


def run_dwg_to_dxf_conversion(job_id: int, **kwargs) -> None:
    """执行一个 DWG→DXF Job（worker 侧，按 status+attempt 守卫）。"""
    from app.modules.cad_processing.dwg_to_dxf.execution import run_dxf_conversion

    run_dxf_conversion(job_id, **kwargs)


def run_dxf_to_dwg_conversion(job_id: int, **kwargs) -> None:
    """执行一个 DXF→DWG Job（worker 侧，按 status+attempt 守卫）。"""
    from app.modules.cad_processing.dxf_to_dwg.execution import run_dxf_to_dwg_conversion as run

    run(job_id, **kwargs)


def run_dxf_to_excel_extraction(job_id: int, **kwargs) -> None:
    """执行一个 DXF→Excel 提取 Job（worker 侧）。"""
    from app.modules.cad_processing.dxf_to_excel.execution import run_dxf2excel_extraction

    run_dxf2excel_extraction(job_id, **kwargs)


def run_dwg_to_dxf_batch(jobs: list[tuple[int, int]], **kwargs) -> dict[str, int]:
    """按目录规模批量执行 DWG→DXF Job（worker 侧）。

    ``jobs`` 是 ``(job_id, attempt)`` 成对列表；每对都是受守卫的执行世代。
    返回汇总 dict。
    """
    from app.modules.cad_processing.dwg_to_dxf.batch import run_dwg_to_dxf_batch as run

    return run(jobs, **kwargs)


def run_dxf_to_dwg_batch(jobs: list[tuple[int, int]], **kwargs) -> dict[str, int]:
    """按目录规模批量执行 DXF→DWG Job（worker 侧）。"""
    from app.modules.cad_processing.dxf_to_dwg.batch import run_dxf_to_dwg_batch as run

    return run(jobs, **kwargs)


def enqueue_dwg_to_dxf_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递一个 DWG→DXF Job 到 Celery；返回 task id。

    ``task_id`` 可钉住稳定 id（dispatch_uid）以实现模糊投递幂等——worker 的
    status/attempt 守卫使重复投递安全。
    """
    from app.modules.cad_processing.tasks import convert_dwg_to_dxf_task

    return str(
        convert_dwg_to_dxf_task.apply_async(
            args=[job_id, attempt], task_id=task_id
        ).id
    )


def enqueue_dwg_to_dxf_batch(
    jobs: list[list[int]], *, task_id: str | None = None
) -> str:
    """投递 DWG→DXF 批次（[job_id, attempt] 列表）到 Celery。"""
    from app.modules.cad_processing.tasks import convert_dwg_to_dxf_batch_task

    return str(convert_dwg_to_dxf_batch_task.apply_async(args=[jobs], task_id=task_id).id)


def enqueue_dxf_to_dwg_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递一个 DXF→DWG Job 到 Celery；返回 task id。"""
    from app.modules.cad_processing.tasks import convert_dxf_to_dwg_task

    return str(
        convert_dxf_to_dwg_task.apply_async(
            args=[job_id, attempt], task_id=task_id
        ).id
    )


def enqueue_dxf_to_dwg_batch(
    jobs: list[list[int]], *, task_id: str | None = None
) -> str:
    """投递 DXF→DWG 批次（[job_id, attempt] 列表）到 Celery。"""
    from app.modules.cad_processing.tasks import convert_dxf_to_dwg_batch_task

    return str(convert_dxf_to_dwg_batch_task.apply_async(args=[jobs], task_id=task_id).id)


def enqueue_dxf_to_excel_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """投递一个 DXF→Excel 提取 Job 到 Celery；返回 task id。"""
    from app.modules.cad_processing.tasks import extract_dxf_to_excel_task

    return str(
        extract_dxf_to_excel_task.apply_async(
            args=[job_id, attempt], task_id=task_id
        ).id
    )


def validate_dxf_source_size(size_bytes: int) -> None:
    """拒绝超过预览大小上限的 DXF 源（413）。"""
    from app.modules.cad_processing.preview import validate_dxf_source_size as validate

    validate(size_bytes)


def preview_batch_name(source):
    """为源对象推导预览批次名。"""
    from app.modules.cad_processing.preview import preview_batch_name as build_name

    return build_name(source)


def get_or_create_dxf_preview(*args, **kwargs):
    """返回（或惰性渲染）某源的鉴权 SVG 预览。

    对大小/复杂度/状态违规抛 AppHTTPException（409/413/422）；遵守
    每次预览的大小/复杂度上限。
    """
    from app.modules.cad_processing.preview import get_or_create_dxf_preview as get_or_create

    return get_or_create(*args, **kwargs)


def invalidate_dxf_previews_for_source(*args, **kwargs) -> None:
    """删除缓存的预览，使下次读取基于源重新渲染。"""
    from app.modules.cad_processing.preview import invalidate_dxf_previews_for_source as invalidate

    invalidate(*args, **kwargs)


__all__ = [
    "MAX_DXF_SIZE_BYTES",
    "convert_dwg_directory",
    "enqueue_dwg_to_dxf_batch",
    "enqueue_dwg_to_dxf_job",
    "enqueue_dxf_to_dwg_batch",
    "enqueue_dxf_to_dwg_job",
    "enqueue_dxf_to_excel_job",
    "get_or_create_dxf_preview",
    "invalidate_dxf_previews_for_source",
    "preview_batch_name",
    "run_dwg_to_dxf_batch",
    "run_dwg_to_dxf_conversion",
    "run_dxf_to_dwg_batch",
    "run_dxf_to_dwg_conversion",
    "run_dxf_to_excel_extraction",
    "validate_dxf_source_size",
]
