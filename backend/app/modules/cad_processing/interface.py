"""Public CAD-processing boundary for file, Job and workflow modules."""

from app.modules.cad_processing.preview import MAX_DXF_SIZE_BYTES


def convert_dwg_directory(inputs, output_dir):
    from app.modules.cad_processing.remnant_conversion import convert_dwg_directory as convert

    return convert(inputs, output_dir)


def run_dwg_to_dxf_conversion(job_id: int, **kwargs) -> None:
    from app.modules.cad_processing.dwg_to_dxf.execution import run_dxf_conversion

    run_dxf_conversion(job_id, **kwargs)


def run_dxf_to_dwg_conversion(job_id: int, **kwargs) -> None:
    from app.modules.cad_processing.dxf_to_dwg.execution import run_dxf_to_dwg_conversion as run

    run(job_id, **kwargs)


def run_dxf_to_excel_extraction(job_id: int, **kwargs) -> None:
    from app.modules.cad_processing.dxf_to_excel.execution import run_dxf2excel_extraction

    run_dxf2excel_extraction(job_id, **kwargs)


def run_dwg_to_dxf_batch(jobs: list[tuple[int, int]], **kwargs) -> dict[str, int]:
    from app.modules.cad_processing.dwg_to_dxf.batch import run_dwg_to_dxf_batch as run

    return run(jobs, **kwargs)


def run_dxf_to_dwg_batch(jobs: list[tuple[int, int]], **kwargs) -> dict[str, int]:
    from app.modules.cad_processing.dxf_to_dwg.batch import run_dxf_to_dwg_batch as run

    return run(jobs, **kwargs)


def enqueue_dwg_to_dxf_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    from app.modules.cad_processing.tasks import convert_dwg_to_dxf_task

    return str(
        convert_dwg_to_dxf_task.apply_async(
            args=[job_id, attempt], task_id=task_id
        ).id
    )


def enqueue_dwg_to_dxf_batch(
    jobs: list[list[int]], *, task_id: str | None = None
) -> str:
    from app.modules.cad_processing.tasks import convert_dwg_to_dxf_batch_task

    return str(convert_dwg_to_dxf_batch_task.apply_async(args=[jobs], task_id=task_id).id)


def enqueue_dxf_to_dwg_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    from app.modules.cad_processing.tasks import convert_dxf_to_dwg_task

    return str(
        convert_dxf_to_dwg_task.apply_async(
            args=[job_id, attempt], task_id=task_id
        ).id
    )


def enqueue_dxf_to_dwg_batch(
    jobs: list[list[int]], *, task_id: str | None = None
) -> str:
    from app.modules.cad_processing.tasks import convert_dxf_to_dwg_batch_task

    return str(convert_dxf_to_dwg_batch_task.apply_async(args=[jobs], task_id=task_id).id)


def enqueue_dxf_to_excel_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    from app.modules.cad_processing.tasks import extract_dxf_to_excel_task

    return str(
        extract_dxf_to_excel_task.apply_async(
            args=[job_id, attempt], task_id=task_id
        ).id
    )


def validate_dxf_source_size(size_bytes: int) -> None:
    from app.modules.cad_processing.preview import validate_dxf_source_size as validate

    validate(size_bytes)


def preview_batch_name(source):
    from app.modules.cad_processing.preview import preview_batch_name as build_name

    return build_name(source)


def get_or_create_dxf_preview(*args, **kwargs):
    from app.modules.cad_processing.preview import get_or_create_dxf_preview as get_or_create

    return get_or_create(*args, **kwargs)


def invalidate_dxf_previews_for_source(*args, **kwargs) -> None:
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
