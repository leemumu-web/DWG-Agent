"""Compose Job, Result and Review routers without changing public prefixes."""

from fastapi import APIRouter

from app.modules.jobs.routes import commands, events, queries, results, reviews

jobs_router = APIRouter()
results_router = APIRouter()
reviews_router = APIRouter()

# Empty-path GET/POST operations cannot be nested under another empty-prefix
# APIRouter in the installed FastAPI version. Extend the already-built routes
# in an explicit order: every static Job path precedes ``/{job_id}``.
for child in (
    queries.static_router,
    commands.static_router,
    events.static_router,
    queries.item_router,
    commands.item_router,
    events.item_router,
    results.job_router,
):
    jobs_router.routes.extend(child.routes)

results_router.routes.extend(results.result_router.routes)
results_router.routes.extend(reviews.result_router.routes)
reviews_router.routes.extend(reviews.review_router.routes)

__all__ = ["jobs_router", "results_router", "reviews_router"]
