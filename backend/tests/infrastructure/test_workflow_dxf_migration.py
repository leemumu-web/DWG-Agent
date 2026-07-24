from __future__ import annotations

import importlib.util
import json
from types import ModuleType

import pytest
import sqlalchemy as sa

from tests.support.paths import REPO_ROOT

MIGRATION_PATH = (
    REPO_ROOT
    / "backend/migrations/versions/c7b2d4e9f601_canonicalize_workflow_dxf_flow.py"
)


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "workflow_dxf_canonical_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema() -> tuple[sa.Engine, dict[str, sa.Table]]:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    tables = {
        "workflows": sa.Table(
            "workflow_runs",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("workflow_type", sa.String(64), nullable=False),
            sa.Column("config_json", sa.Text),
        ),
        "stages": sa.Table(
            "workflow_stage_runs",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("workflow_run_id", sa.Integer, nullable=False),
            sa.Column("stage_code", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, default="pending"),
            sa.Column("progress", sa.Integer, nullable=False, default=0),
        ),
        "artifacts": sa.Table(
            "workflow_artifacts",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("workflow_run_id", sa.Integer, nullable=False),
            sa.Column("stage_run_id", sa.Integer),
            sa.Column("artifact_type", sa.String(64), nullable=False),
            sa.Column("file_id", sa.Integer),
        ),
        "batches": sa.Table(
            "workflow_input_batches",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("workflow_run_id", sa.Integer, nullable=False),
        ),
        "items": sa.Table(
            "workflow_input_items",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("input_batch_id", sa.Integer, nullable=False),
            sa.Column("file_id", sa.Integer, nullable=False),
            sa.Column("role", sa.String(32), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("derived_dxf_file_id", sa.Integer),
            sa.Column("drawing_id", sa.Integer),
        ),
        "versions": sa.Table(
            "drawing_versions",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("drawing_id", sa.Integer, nullable=False),
            sa.Column("file_id", sa.Integer, nullable=False),
            sa.Column("source", sa.String(64), nullable=False),
        ),
        "files": sa.Table(
            "files",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("file_ext", sa.String(16)),
            sa.Column("status", sa.String(32), nullable=False),
        ),
    }
    metadata.create_all(engine)
    return engine, tables


def _seed_revision_two(connection, tables: dict[str, sa.Table]) -> None:
    connection.execute(
        tables["workflows"].insert().values(
            id=1,
            workflow_type="linux_production",
            config_json=json.dumps({"definition_revision": 2, "kept": True}),
        )
    )
    stages = {
        "source_intake": 101,
        "dxf_classification": 102,
        "drawing_processing": 103,
        "excel_stage1": 104,
        "design_barrier": 105,
        "cam_packaging": 106,
        "windows_cam": 107,
        "result_acceptance": 108,
        "delivery_archive": 109,
    }
    connection.execute(
        tables["stages"].insert(),
        [
            {
                "id": stage_id,
                "workflow_run_id": 1,
                "stage_code": stage_code,
                "status": "pending",
                "progress": 0,
            }
            for stage_code, stage_id in stages.items()
        ],
    )
    connection.execute(
        tables["files"].insert(),
        [
            {"id": 10, "file_ext": ".dwg", "status": "available"},
            {"id": 11, "file_ext": ".dxf", "status": "available"},
            {"id": 12, "file_ext": ".dxf", "status": "available"},
            {"id": 13, "file_ext": ".dxf", "status": "available"},
            {"id": 14, "file_ext": ".dxf", "status": "available"},
            {"id": 15, "file_ext": ".xlsx", "status": "available"},
        ],
    )
    connection.execute(
        tables["artifacts"].insert(),
        [
            {
                "id": 1,
                "workflow_run_id": 1,
                "stage_run_id": stages["source_intake"],
                "artifact_type": "source_file",
                "file_id": 10,
            },
            {
                "id": 2,
                "workflow_run_id": 1,
                "stage_run_id": stages["source_intake"],
                "artifact_type": "derived_dxf",
                "file_id": 11,
            },
            {
                "id": 3,
                "workflow_run_id": 1,
                "stage_run_id": stages["drawing_processing"],
                "artifact_type": "processed_drawing",
                "file_id": 12,
            },
            {
                "id": 4,
                "workflow_run_id": 1,
                "stage_run_id": stages["windows_cam"],
                "artifact_type": "cam_result",
                "file_id": 13,
            },
            {
                "id": 5,
                "workflow_run_id": 1,
                "stage_run_id": stages["delivery_archive"],
                "artifact_type": "delivery_file",
                "file_id": 14,
            },
        ],
    )
    connection.execute(
        tables["batches"].insert().values(id=1, workflow_run_id=1)
    )
    connection.execute(
        tables["items"].insert().values(
            id=1,
            input_batch_id=1,
            file_id=10,
            role="source_dwg",
            status="frozen",
            derived_dxf_file_id=11,
            drawing_id=500,
        )
    )
    connection.execute(
        tables["versions"].insert().values(
            id=1,
            drawing_id=500,
            file_id=10,
            source="workflow_input_dwg",
        )
    )


def _artifact_types(connection, tables: dict[str, sa.Table]) -> list[str]:
    return list(
        connection.scalars(
            sa.select(tables["artifacts"].c.artifact_type).order_by(
                tables["artifacts"].c.id
            )
        )
    )


def test_revision_two_upgrade_canonicalizes_artifacts_and_drawing_version():
    migration = _migration_module()
    engine, tables = _schema()
    with engine.begin() as connection:
        _seed_revision_two(connection, tables)

        migration._upgrade_linux_workflows(connection)

        assert _artifact_types(connection, tables) == [
            "source_dwg",
            "canonical_dxf",
            "processed_dxf",
            "cam_output_dxf",
            "delivery_dxf",
        ]
        version = connection.execute(
            sa.select(
                tables["versions"].c.file_id,
                tables["versions"].c.source,
            )
        ).one()
        assert version == (11, "workflow_input_dxf")
        config = json.loads(
            connection.scalar(sa.select(tables["workflows"].c.config_json))
        )
        assert config == {"definition_revision": 3, "kept": True}


def test_upgrade_rejects_frozen_item_without_derived_dxf():
    migration = _migration_module()
    engine, tables = _schema()
    with engine.begin() as connection:
        _seed_revision_two(connection, tables)
        connection.execute(
            tables["items"].update().values(derived_dxf_file_id=None)
        )

        with pytest.raises(RuntimeError, match="missing derived DXF"):
            migration._upgrade_linux_workflows(connection)


def test_upgrade_rejects_generic_cam_package_with_unknown_format():
    migration = _migration_module()
    engine, tables = _schema()
    with engine.begin() as connection:
        _seed_revision_two(connection, tables)
        connection.execute(
            tables["artifacts"].insert().values(
                id=6,
                workflow_run_id=1,
                stage_run_id=106,
                artifact_type="cam_package",
                file_id=15,
            )
        )

        with pytest.raises(RuntimeError, match="cannot infer revision 3 artifact"):
            migration._upgrade_linux_workflows(connection)


def test_upgrade_rejects_progressed_result_acceptance_without_accepted_dxf():
    migration = _migration_module()
    engine, tables = _schema()
    with engine.begin() as connection:
        _seed_revision_two(connection, tables)
        connection.execute(
            tables["stages"].update()
            .where(tables["stages"].c.id == 108)
            .values(status="succeeded", progress=100)
        )

        with pytest.raises(RuntimeError, match="progressed result_acceptance"):
            migration._upgrade_linux_workflows(connection)


def test_upgrade_rejects_legacy_dxf_artifact_pointing_to_excel():
    migration = _migration_module()
    engine, tables = _schema()
    with engine.begin() as connection:
        _seed_revision_two(connection, tables)
        connection.execute(
            tables["artifacts"].update()
            .where(tables["artifacts"].c.id == 2)
            .values(file_id=15)
        )

        with pytest.raises(RuntimeError, match="does not reference a DXF"):
            migration._upgrade_linux_workflows(connection)


def test_safe_revision_three_downgrade_restores_revision_two_names():
    migration = _migration_module()
    engine, tables = _schema()
    with engine.begin() as connection:
        _seed_revision_two(connection, tables)
        migration._upgrade_linux_workflows(connection)

        migration._downgrade_linux_workflows(connection)

        assert _artifact_types(connection, tables) == [
            "source_file",
            "derived_dxf",
            "processed_drawing",
            "cam_result",
            "delivery_file",
        ]
        version = connection.execute(
            sa.select(
                tables["versions"].c.file_id,
                tables["versions"].c.source,
            )
        ).one()
        assert version == (10, "workflow_input_dwg")
        config = json.loads(
            connection.scalar(sa.select(tables["workflows"].c.config_json))
        )
        assert config == {"definition_revision": 2, "kept": True}


def test_downgrade_rejects_new_artifacts_without_revision_two_equivalent():
    migration = _migration_module()
    engine, tables = _schema()
    with engine.begin() as connection:
        _seed_revision_two(connection, tables)
        migration._upgrade_linux_workflows(connection)
        connection.execute(
            tables["artifacts"].insert().values(
                id=6,
                workflow_run_id=1,
                stage_run_id=106,
                artifact_type="cam_input_dxf",
                file_id=13,
            )
        )

        with pytest.raises(RuntimeError, match="cannot be represented by revision 2"):
            migration._downgrade_linux_workflows(connection)
