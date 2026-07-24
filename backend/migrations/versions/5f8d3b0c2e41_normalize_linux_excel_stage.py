"""normalize the Linux workflow Excel stage

Revision ID: 5f8d3b0c2e41
Revises: 4e7c2a9b1d30
Create Date: 2026-07-24
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "5f8d3b0c2e41"
down_revision: str | None = "4e7c2a9b1d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXCEL_STAGE_CODES = {"excel_stage1", "excel_final"}
_NEW_EXCEL_STAGE_NAME = "Excel 第一阶段处理"
_LEGACY_EXCEL_FINAL_NAME = "Excel 最终合并"


def _config_object(value: object, *, workflow_id: int) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"linux_production workflow {workflow_id} has invalid config_json"
            ) from exc
    if not isinstance(value, dict):
        raise RuntimeError(
            f"linux_production workflow {workflow_id} config_json is not an object"
        )
    return dict(value)


def _stage_has_execution_evidence(
    stage: Mapping[str, object],
    *,
    allowed_statuses: set[str],
    artifact_count: int,
) -> bool:
    if stage["status"] not in allowed_statuses:
        return True
    if stage["progress"] not in (None, 0):
        return True
    if artifact_count:
        return True
    return any(
        stage[field] is not None
        for field in (
            "job_id",
            "job_attempt",
            "input_json",
            "output_json",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
        )
    )


def _workflow_rows(connection: Connection) -> list[Mapping[str, object]]:
    return list(
        connection.execute(
            sa.text(
                """
                SELECT id, current_stage, status, config_json
                FROM workflow_runs
                WHERE workflow_type = 'linux_production'
                ORDER BY id
                """
            )
        ).mappings()
    )


def _workflow_stages(
    connection: Connection,
    workflow_id: int,
) -> list[Mapping[str, object]]:
    return list(
        connection.execute(
            sa.text(
                """
                SELECT id, stage_code, sequence, status, job_id, job_attempt,
                       progress, input_json, output_json, error_code,
                       error_message, started_at, finished_at
                FROM workflow_stage_runs
                WHERE workflow_run_id = :workflow_id
                ORDER BY sequence, id
                """
            ),
            {"workflow_id": workflow_id},
        ).mappings()
    )


def _excel_artifact_count(
    connection: Connection,
    workflow_id: int,
    stage_ids: tuple[int, ...],
) -> int:
    stage_id_filters = " OR ".join(
        f"stage_run_id = :stage_id_{index}" for index in range(len(stage_ids))
    )
    parameters: dict[str, object] = {"workflow_id": workflow_id}
    parameters.update(
        {
            f"stage_id_{index}": stage_id
            for index, stage_id in enumerate(stage_ids)
        }
    )
    return int(
        connection.execute(
            sa.text(
                f"""
                SELECT COUNT(*)
                FROM workflow_artifacts
                WHERE workflow_run_id = :workflow_id
                  AND (
                      artifact_type IN ('stage1_excel', 'final_excel')
                      OR {stage_id_filters}
                  )
                """
            ),
            parameters,
        ).scalar_one()
    )


def _store_definition_revision(
    connection: Connection,
    workflow: Mapping[str, object],
    definition_revision: int,
) -> None:
    workflow_id = int(workflow["id"])
    config = _config_object(workflow["config_json"], workflow_id=workflow_id)
    config["definition_revision"] = definition_revision
    connection.execute(
        sa.text(
            """
            UPDATE workflow_runs
            SET config_json = :config_json
            WHERE id = :workflow_id
            """
        ),
        {
            "config_json": json.dumps(
                config,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "workflow_id": workflow_id,
        },
    )


def _normalize_linux_workflows(connection: Connection) -> None:
    for workflow in _workflow_rows(connection):
        workflow_id = int(workflow["id"])
        if workflow["current_stage"] == "excel_final":
            raise RuntimeError(
                f"linux_production workflow {workflow_id} is currently at legacy excel_final"
            )
        stages = _workflow_stages(connection, workflow_id)
        by_code = {str(stage["stage_code"]): stage for stage in stages}
        if not _EXCEL_STAGE_CODES <= by_code.keys():
            raise RuntimeError(
                f"linux_production workflow {workflow_id} does not have the expected "
                "legacy Excel stages"
            )
        excel_stage1 = by_code["excel_stage1"]
        excel_final = by_code["excel_final"]
        artifact_count = _excel_artifact_count(
            connection,
            workflow_id,
            (int(excel_stage1["id"]), int(excel_final["id"])),
        )
        if _stage_has_execution_evidence(
            excel_stage1,
            allowed_statuses={"pending", "ready", "waiting_input"},
            artifact_count=artifact_count,
        ) or _stage_has_execution_evidence(
            excel_final,
            allowed_statuses={"pending"},
            artifact_count=artifact_count,
        ):
            raise RuntimeError(
                f"linux_production workflow {workflow_id} has legacy Excel execution evidence"
            )

        final_sequence = int(excel_final["sequence"])
        connection.execute(
            sa.text(
                """
                DELETE FROM workflow_stage_runs
                WHERE id = :stage_id
                """
            ),
            {"stage_id": int(excel_final["id"])},
        )
        connection.execute(
            sa.text(
                """
                UPDATE workflow_stage_runs
                SET sequence = sequence - 1
                WHERE workflow_run_id = :workflow_id
                  AND sequence > :final_sequence
                """
            ),
            {
                "workflow_id": workflow_id,
                "final_sequence": final_sequence,
            },
        )
        connection.execute(
            sa.text(
                """
                UPDATE workflow_stage_runs
                SET name = :name
                WHERE id = :stage_id
                """
            ),
            {
                "name": _NEW_EXCEL_STAGE_NAME,
                "stage_id": int(excel_stage1["id"]),
            },
        )
        _store_definition_revision(connection, workflow, 2)


def _restore_legacy_linux_workflows(connection: Connection) -> None:
    for workflow in _workflow_rows(connection):
        workflow_id = int(workflow["id"])
        stages = _workflow_stages(connection, workflow_id)
        by_code = {str(stage["stage_code"]): stage for stage in stages}
        if "excel_final" in by_code or "excel_stage1" not in by_code:
            raise RuntimeError(
                f"linux_production workflow {workflow_id} is not in the normalized "
                "nine-stage shape"
            )
        excel_stage1 = by_code["excel_stage1"]
        current_stage = str(workflow["current_stage"] or "")
        current = by_code.get(current_stage)
        if current is not None and int(current["sequence"]) > int(excel_stage1["sequence"]):
            raise RuntimeError(
                f"linux_production workflow {workflow_id} has advanced past excel_stage1"
            )
        if _stage_has_execution_evidence(
            excel_stage1,
            allowed_statuses={"pending", "ready", "waiting_input"},
            artifact_count=_excel_artifact_count(
                connection,
                workflow_id,
                (int(excel_stage1["id"]),),
            ),
        ):
            raise RuntimeError(
                f"linux_production workflow {workflow_id} has Excel stage execution evidence"
            )

        connection.execute(
            sa.text(
                """
                UPDATE workflow_stage_runs
                SET sequence = sequence + 1
                WHERE workflow_run_id = :workflow_id
                  AND sequence >= 6
                """
            ),
            {"workflow_id": workflow_id},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO workflow_stage_runs (
                    workflow_run_id, stage_code, name, sequence, status, progress
                )
                VALUES (
                    :workflow_id, 'excel_final', :name, 6, 'pending', 0
                )
                """
            ),
            {
                "workflow_id": workflow_id,
                "name": _LEGACY_EXCEL_FINAL_NAME,
            },
        )
        _store_definition_revision(connection, workflow, 1)


def upgrade() -> None:
    _normalize_linux_workflows(op.get_bind())


def downgrade() -> None:
    _restore_legacy_linux_workflows(op.get_bind())
