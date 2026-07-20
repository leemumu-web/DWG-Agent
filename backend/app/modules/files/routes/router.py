"""File HTTP composition with static paths ahead of item parameters."""

from fastapi import APIRouter

from app.modules.files.routes import batches, catalog, downloads, previews, uploads

router = APIRouter()

# FastAPI rejects nesting an empty-path operation into an empty-prefix router.
# Extending already-built routes preserves the exact public ``/files`` path while
# still keeping handlers physically separated and the precedence explicit here.
for child in (
    uploads.router,
    catalog.static_router,
    batches.router,
    previews.router,
    downloads.static_router,
    catalog.item_router,
    downloads.item_router,
):
    router.routes.extend(child.routes)

__all__ = ["router"]
