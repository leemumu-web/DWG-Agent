"""Workflow template capability endpoints."""

from fastapi import APIRouter, Request

from app.modules.identity.interface import CurrentUser
from app.modules.workflows.templates import list_workflow_templates
from app.platform.http.envelopes import ok

router = APIRouter()


@router.get(
    "/templates",
    summary="列出工作流模板与阶段能力",
    description="返回后端权威的阶段顺序、执行方式、实现状态和输入输出契约。",
)
def get_workflow_templates(request: Request, current_user: CurrentUser):
    return ok(list_workflow_templates(), request.state.request_id)
