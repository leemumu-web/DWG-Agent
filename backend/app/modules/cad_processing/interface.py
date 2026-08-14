"""Public CAD-processing boundary for file, Job and workflow modules.

Calling contract (CONTEXT.md: Interface must document invariants, error
modes, ordering and configuration):

- Execution vs. dispatch: ``run_*_conversion(job_id)`` and ``run_*_batch``
  are the worker-side execution entry points — they claim the Job (fenced by
  status + attempt) inside the Celery task. ``enqueue_*`` are the
  dispatch-side entry points (``apply_async``); enqueue first, then let the
  worker execute. Batch functions take ``(job_id, attempt)`` pairs; do not
  pass bare job ids.
- ``convert_dwg_directory`` is the remnant-domain adapter for one
  directory-sized ODA call; it does not own a ledger and silently omits
  failed drawings from its result.
- Previews: ``get_or_create_dxf_preview`` raises ``AppHTTPException``
  (409/413/422) for size/complexity/state violations; call
  ``validate_dxf_source_size`` before rendering and respect
  ``MAX_DXF_SIZE_BYTES``. Preview artifacts are cached and keyed by source
  hash; invalidate via ``invalidate_dxf_previews_for_source`` when the
  source changes.
"""

from app.modules.cad_processing.preview import MAX_DXF_SIZE_BYTES


def convert_dwg_directory(inputs, output_dir):
    """Convert a directory of DWG inputs in one ODA call (remnant domain).

    Directory-level adapter for the remnant pipeline: returns only
    successful items; failed drawings are silently omitted — callers must
    surface partial failure themselves.
    """
    from app.modules.cad_processing.remnant_conversion import convert_dwg_directory as convert

    return convert(inputs, output_dir)


def run_dwg_to_dxf_conversion(job_id: int, **kwargs) -> None:
    """Execute one DWG→DXF Job (worker side, fenced by status + attempt)."""
    from app.modules.cad_processing.dwg_to_dxf.execution import run_dxf_conversion

    run_dxf_conversion(job_id, **kwargs)


def run_dxf_to_dwg_conversion(job_id: int, **kwargs) -> None:
    """Execute one DXF→DWG Job (worker side, fenced by status + attempt)."""
    from app.modules.cad_processing.dxf_to_dwg.execution import run_dxf_to_dwg_conversion as run

    run(job_id, **kwargs)


def run_dxf_to_excel_extraction(job_id: int, **kwargs) -> None:
    """Execute one DXF→Excel extraction Job (worker side)."""
    from app.modules.cad_processing.dxf_to_excel.execution import run_dxf2excel_extraction

    run_dxf2excel_extraction(job_id, **kwargs)


def run_dwg_to_dxf_batch(jobs: list[tuple[int, int]], **kwargs) -> dict[str, int]:
    """Execute DWG→DXF Jobs in directory-sized batches (worker side).

    ``jobs`` is a list of ``(job_id, attempt)`` pairs; each pair is the
    fenced execution generation. Returns a summary dict.
    """
    from app.modules.cad_processing.dwg_to_dxf.batch import run_dwg_to_dxf_batch as run

    return run(jobs, **kwargs)


def run_dxf_to_dwg_batch(jobs: list[tuple[int, int]], **kwargs) -> dict[str, int]:
    """Execute DXF→DWG Jobs in directory-sized batches (worker side)."""
    from app.modules.cad_processing.dxf_to_dwg.batch import run_dxf_to_dwg_batch as run

    return run(jobs, **kwargs)


def enqueue_dwg_to_dxf_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """Dispatch one DWG→DXF Job to Celery; returns the task id.

    ``task_id`` may pin a stable id (dispatch_uid) for ambiguous-delivery
    idempotency — the worker's status/attempt guards make duplicate delivery
    safe.
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
    """Dispatch a DWG→DXF batch (list of [job_id, attempt]) to Celery."""
    from app.modules.cad_processing.tasks import convert_dwg_to_dxf_batch_task

    return str(convert_dwg_to_dxf_batch_task.apply_async(args=[jobs], task_id=task_id).id)


def enqueue_dxf_to_dwg_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """Dispatch one DXF→DWG Job to Celery; returns the task id."""
    from app.modules.cad_processing.tasks import convert_dxf_to_dwg_task

    return str(
        convert_dxf_to_dwg_task.apply_async(
            args=[job_id, attempt], task_id=task_id
        ).id
    )


def enqueue_dxf_to_dwg_batch(
    jobs: list[list[int]], *, task_id: str | None = None
) -> str:
    """Dispatch a DXF→DWG batch (list of [job_id, attempt]) to Celery."""
    from app.modules.cad_processing.tasks import convert_dxf_to_dwg_batch_task

    return str(convert_dxf_to_dwg_batch_task.apply_async(args=[jobs], task_id=task_id).id)


def enqueue_dxf_to_excel_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """Dispatch one DXF→Excel extraction Job to Celery; returns the task id."""
    from app.modules.cad_processing.tasks import extract_dxf_to_excel_task

    return str(
        extract_dxf_to_excel_task.apply_async(
            args=[job_id, attempt], task_id=task_id
        ).id
    )


def validate_dxf_source_size(size_bytes: int) -> None:
    """Reject DXF sources above the preview size limit (413)."""
    from app.modules.cad_processing.preview import validate_dxf_source_size as validate

    validate(size_bytes)


def preview_batch_name(source):
    """Derive the preview batch name for a source object."""
    from app.modules.cad_processing.preview import preview_batch_name as build_name

    return build_name(source)


def get_or_create_dxf_preview(*args, **kwargs):
    """Return (or lazily render) the authenticated SVG preview for a source.

    Raises AppHTTPException (409/413/422) for size, complexity and state
    violations; honors per-preview size/complexity caps.
    """
    from app.modules.cad_processing.preview import get_or_create_dxf_preview as get_or_create

    return get_or_create(*args, **kwargs)


def invalidate_dxf_previews_for_source(*args, **kwargs) -> None:
    """Drop cached previews so the next read re-renders from the source."""
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
