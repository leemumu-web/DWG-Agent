from __future__ import annotations

import importlib.util
import json
from types import ModuleType

import pytest
import sqlalchemy as sa

from tests.support.paths import REPO_ROOT

MIGRATION_PATH = (
    REPO_ROOT
    / "backend/migrations/versions/5f8d3b0c2e41_normalize_linux_excel_stage.py"
)
LEGACY_STAGE_CODES = (
    "source_intake",
    "dxf_classification",
    "drawing_processing",
    "excel_stage1",
    "design_barrier",
    "excel_final",
    "cam_packaging",
    "windows_cam",
    "result_acceptance",
    "delivery_archive",
)
NORMALIZED_STAGE_CODES = tuple(
    code for code in LEGACY_STAGE_CODES if code != "excel_final"
)


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "workflow_excel_stage_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema() -> tuple[sa.Engine, sa.Table, sa.Table, sa.Table]:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    workflows = sa.Table(
        "workflow_runs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("workflow_type", sa.String(64), nullable=False),
        sa.Column("current_stage", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("config_json", sa.Text),
    )
    stages = sa.Table(
        "workflow_stage_runs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("workflow_run_id", sa.Integer, nullable=False),
        sa.Column("stage_code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("job_id", sa.Integer),
        sa.Column("job_attempt", sa.Integer),
        sa.Column("progress", sa.Integer, nullable=False),
        sa.Column("input_json", sa.Text),
        sa.Column("output_json", sa.Text),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text),
        sa.Column("started_at", sa.DateTime),
        sa.Column("finished_at", sa.DateTime),
    )
    artifacts = sa.Table(
        "workflow_artifacts",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("workflow_run_id", sa.Integer, nullable=False),
        sa.Column("stage_run_id", sa.Integer),
        sa.Column("artifact_type", sa.String(64), nullable=False),
    )
    metadata.create_all(engine)
    return engine, workflows, stages, artifacts


def _insert_legacy_workflow(
    connection,
    workflows: sa.Table,
    stages: sa.Table,
    *,
    workflow_id: int,
    excel_job_id: int | None = None,
) -> None:
    connection.execute(
        workflows.insert().values(
            id=workflow_id,
            workflow_type="linux_production",
            current_stage="source_intake",
            status="waiting_input",
            config_json=json.dumps({"kept": True}),
        )
    )
    connection.execute(
        stages.insert(),
        [
            {
                "id": workflow_id * 100 + sequence,
                "workflow_run_id": workflow_id,
                "stage_code": code,
                "name": code,
                "sequence": sequence,
                "status": "ready" if sequence == 1 else "pending",
                "job_id": excel_job_id if code == "excel_stage1" else None,
                "job_attempt": 1 if code == "excel_stage1" and excel_job_id else None,
                "progress": 0,
            }
            for sequence, code in enumerate(LEGACY_STAGE_CODES, start=1)
        ],
    )


def test_safe_legacy_shape_normalizes_and_downgrades_without_losing_config():
    migration = _migration_module()
    engine, workflows, stages, _ = _schema()
    with engine.begin() as connection:
        _insert_legacy_workflow(
            connection,
            workflows,
            stages,
            workflow_id=1,
        )
        migration._normalize_linux_workflows(connection)

        normalized = connection.execute(
            sa.select(stages.c.stage_code, stages.c.sequence)
            .where(stages.c.workflow_run_id == 1)
            .order_by(stages.c.sequence)
        ).all()
        config = json.loads(
            connection.execute(
                sa.select(workflows.c.config_json).where(workflows.c.id == 1)
            ).scalar_one()
        )
        assert tuple(row.stage_code for row in normalized) == NORMALIZED_STAGE_CODES
        assert tuple(row.sequence for row in normalized) == tuple(range(1, 10))
        assert config == {"definition_revision": 2, "kept": True}

        migration._restore_legacy_linux_workflows(connection)
        restored = connection.execute(
            sa.select(stages.c.stage_code, stages.c.sequence)
            .where(stages.c.workflow_run_id == 1)
            .order_by(stages.c.sequence)
        ).all()
        restored_config = json.loads(
            connection.execute(
                sa.select(workflows.c.config_json).where(workflows.c.id == 1)
            ).scalar_one()
        )
        assert tuple(row.stage_code for row in restored) == LEGACY_STAGE_CODES
        assert tuple(row.sequence for row in restored) == tuple(range(1, 11))
        assert restored_config == {"definition_revision": 1, "kept": True}


def test_legacy_excel_job_evidence_aborts_normalization():
    migration = _migration_module()
    engine, workflows, stages, _ = _schema()
    with engine.begin() as connection:
        _insert_legacy_workflow(
            connection,
            workflows,
            stages,
            workflow_id=7,
            excel_job_id=99,
        )

        with pytest.raises(
            RuntimeError,
            match="workflow 7 has legacy Excel execution evidence",
        ):
            migration._normalize_linux_workflows(connection)

        assert connection.scalar(
            sa.select(sa.func.count())
            .select_from(stages)
            .where(stages.c.workflow_run_id == 7)
        ) == 10
