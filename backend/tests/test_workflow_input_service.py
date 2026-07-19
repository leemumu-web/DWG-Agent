from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.file import StoredFile
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.models.workflow_input import WorkflowInputBatch, WorkflowInputItem
from app.schemas.workflow_schema import WorkflowCreate
from app.services import workflow_service


def _workflow(db):
    user = User(
        username=f"input-owner-{uuid4().hex[:8]}",
        password_hash="x",
        real_name="Input Owner",
        status="active",
    )
    db.add(user)
    db.flush()
    project = Project(
        code=f"INPUT-{uuid4().hex[:6]}",
        name="Production Input",
        owner_id=user.id,
        status="active",
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, project_role="project_owner"))
    workflow = workflow_service.create_workflow(
        db,
        WorkflowCreate(
            project_id=project.id,
            name="Input freeze",
            workflow_type="linux_production",
        ),
        created_by=user.id,
    )
    db.flush()
    return user, project, workflow


def _file(db, name: str) -> StoredFile:
    stored = StoredFile(
        bucket="test",
        storage_key=f"inputs/{uuid4().hex}/{name}",
        original_name=name,
        file_ext=f".{name.rsplit('.', 1)[-1].lower()}",
        content_type="application/octet-stream",
        size_bytes=2048,
        sha256=uuid4().hex + uuid4().hex,
        status="available",
    )
    db.add(stored)
    db.flush()
    return stored


def test_input_batch_model_has_one_batch_per_workflow_and_ordered_items(db):
    user, project, workflow = _workflow(db)
    first_dwg = _file(db, "B.dwg")
    second_dwg = _file(db, "A.dwg")
    excel = _file(db, "parts.xlsx")
    batch = WorkflowInputBatch(
        workflow_run_id=workflow.id,
        project_id=project.id,
        created_by=user.id,
        status="uploading",
        version=1,
    )
    db.add(batch)
    db.flush()
    db.add_all(
        [
            WorkflowInputItem(
                input_batch_id=batch.id,
                file_id=first_dwg.id,
                role="source_dwg",
                original_name=first_dwg.original_name,
                normalized_stem="b",
                status="uploaded",
            ),
            WorkflowInputItem(
                input_batch_id=batch.id,
                file_id=second_dwg.id,
                role="source_dwg",
                original_name=second_dwg.original_name,
                normalized_stem="a",
                status="uploaded",
            ),
            WorkflowInputItem(
                input_batch_id=batch.id,
                file_id=excel.id,
                role="source_excel",
                original_name=excel.original_name,
                normalized_stem="parts",
                status="uploaded",
            ),
        ]
    )
    db.flush()

    assert workflow.input_batch.id == batch.id
    assert [item.original_name for item in batch.items] == ["B.dwg", "A.dwg", "parts.xlsx"]

    db.add(
        WorkflowInputBatch(
            workflow_run_id=workflow.id,
            project_id=project.id,
            created_by=user.id,
            status="uploading",
            version=1,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
