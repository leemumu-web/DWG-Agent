"""Stable workflow HTTP composition in observable registration order."""

from fastapi import APIRouter

from app.modules.workflows.routes.artifacts import router as artifacts_router
from app.modules.workflows.routes.classification import router as classification_router
from app.modules.workflows.routes.commands import (
    collection_router as command_collection_router,
)
from app.modules.workflows.routes.commands import detail_router as command_detail_router
from app.modules.workflows.routes.execution import router as execution_router
from app.modules.workflows.routes.intake import router as intake_router
from app.modules.workflows.routes.queries import (
    collection_router as query_collection_router,
)
from app.modules.workflows.routes.queries import detail_router as query_detail_router
from app.modules.workflows.routes.templates import router as templates_router

router = APIRouter()


def _mount(child: APIRouter, *, tag: str) -> None:
    """Mount zero-prefix child routes, including the collection's empty path."""
    for route in child.routes:
        route.tags = [tag, *route.tags]
        router.routes.append(route)


_mount(templates_router, tag="workflows")
_mount(query_collection_router, tag="workflows")
_mount(command_collection_router, tag="workflows")
_mount(artifacts_router, tag="workflows")
_mount(execution_router, tag="workflows")
_mount(classification_router, tag="workflows")
_mount(query_detail_router, tag="workflows")
_mount(command_detail_router, tag="workflows")
_mount(intake_router, tag="workflow-inputs")
