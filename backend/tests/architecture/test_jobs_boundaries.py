from __future__ import annotations

import ast
import importlib
from pathlib import Path

from tests.support.paths import REPO_ROOT

APP_ROOT = REPO_ROOT / "backend" / "app"

JOB_TABLES = {
    "analysis_results",
    "job_dispatches",
    "job_steps",
    "jobs",
    "review_records",
}

EXPECTED_JOB_ROUTES = [
    (("GET",), "", "list_jobs"),
    (("POST",), "", "create_job_api"),
    (("POST",), "/batches", "create_conversion_batch"),
    (("POST",), "/cancellation-requests", "cancel_jobs"),
    (("POST",), "/cancel-all-active", "cancel_all_active"),
    (("GET",), "/events/stream", "get_conversion_events"),
    (("GET",), "/{job_id}", "get_job"),
    (("GET",), "/{job_id}/steps", "get_job_steps"),
    (("GET",), "/{job_id}/logs", "get_job_logs"),
    (("POST",), "/{job_id}/cancellation-requests", "cancel_job"),
    (("POST",), "/{job_id}/retry-requests", "retry_job"),
    (("GET",), "/{job_id}/events", "get_job_events"),
    (("GET",), "/{job_id}/results", "get_job_results"),
]

EXPECTED_RESULT_ROUTES = [
    (("GET",), "/{result_id}", "get_result"),
    (("GET",), "/{result_id}/download-url", "get_result_download_url"),
    (("POST",), "/{result_id}/reviews", "create_review"),
    (("GET",), "/{result_id}/reviews", "list_result_reviews"),
]

EXPECTED_REVIEW_ROUTES = [(("GET",), "/pending", "list_pending_reviews")]

PUBLIC_JOB_CONTRACT = {
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
    "rerun_succeeded_job",
    "retry_job",
    "run_local_stub_job",
    "summarize_job_execution",
    "stage_conversion_dispatch",
    "stage_job_dispatch",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _routes(router) -> list[tuple[tuple[str, ...], str, str]]:
    return [(tuple(sorted(route.methods or ())), route.path, route.name) for route in router.routes]


def test_jobs_interface_owns_exact_lifecycle_tables() -> None:
    jobs = importlib.import_module("app.modules.jobs.interface")

    owned = {
        jobs.Job.__table__.name,
        jobs.JobDispatch.__table__.name,
        jobs.JobStep.__table__.name,
        jobs.AnalysisResult.__table__.name,
        jobs.ReviewRecord.__table__.name,
    }

    assert owned == JOB_TABLES


def test_jobs_interface_exposes_cross_domain_contract() -> None:
    jobs = importlib.import_module("app.modules.jobs.interface")

    assert set(jobs.__all__) == PUBLIC_JOB_CONTRACT


def test_jobs_routers_preserve_contract_and_static_precedence() -> None:
    module = importlib.import_module("app.modules.jobs.routes.router")

    job_routes = _routes(module.jobs_router)
    assert job_routes == EXPECTED_JOB_ROUTES
    first_item = job_routes.index((("GET",), "/{job_id}", "get_job"))
    assert all("{job_id}" not in path for _methods, path, _name in job_routes[:first_item])
    assert _routes(module.results_router) == EXPECTED_RESULT_ROUTES
    assert _routes(module.reviews_router) == EXPECTED_REVIEW_ROUTES


def test_other_modules_use_only_jobs_interface() -> None:
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(APP_ROOT)
        if relative.parts[:2] == ("modules", "jobs") or relative.parts[:1] == ("bootstrap",):
            continue
        for imported in _imports(path):
            prefix = "app.modules.jobs"
            if imported.startswith(prefix) and not imported.startswith(f"{prefix}.interface"):
                violations.append(f"{relative} -> {imported}")

    assert violations == []


def test_platform_does_not_import_jobs_business_boundary() -> None:
    violations = [
        f"{path.relative_to(APP_ROOT)} -> {imported}"
        for path in sorted((APP_ROOT / "platform").rglob("*.py"))
        for imported in _imports(path)
        if imported.startswith("app.modules.jobs")
    ]

    assert violations == []


def test_jobs_interface_does_not_compose_http_routes() -> None:
    path = APP_ROOT / "modules" / "jobs" / "interface.py"

    assert not any(imported.startswith("app.modules.jobs.routes") for imported in _imports(path))


def test_legacy_job_lifecycle_files_are_retired() -> None:
    retired = (
        "api/v1/jobs_api.py",
        "api/v1/results_api.py",
        "api/v1/reviews_api.py",
        "models/job.py",
        "models/result.py",
        "schemas/job_schema.py",
        "schemas/result_schema.py",
        "services/job_access.py",
        "services/job_events.py",
        "services/job_service.py",
        "services/review_service.py",
    )

    assert [path for path in retired if (APP_ROOT / path).exists()] == []
