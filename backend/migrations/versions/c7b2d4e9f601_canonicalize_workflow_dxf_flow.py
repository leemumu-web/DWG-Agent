"""canonicalize Linux workflow DXF lineage

Revision ID: c7b2d4e9f601
Revises: 8a6c1f4e2b90
Create Date: 2026-07-24
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "c7b2d4e9f601"
down_revision: str | None = "8a6c1f4e2b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE_ARTIFACTS = {
    ("source_intake", "source_file"): ("source_dwg", ".dwg"),
    ("source_intake", "derived_dxf"): ("canonical_dxf", ".dxf"),
    ("drawing_processing", "processed_drawing"): ("processed_dxf", ".dxf"),
    ("windows_cam", "cam_result"): ("cam_output_dxf", ".dxf"),
    ("delivery_archive", "delivery_file"): ("delivery_dxf", ".dxf"),
}
_DOWNGRADE_ARTIFACTS = {
    ("source_intake", "source_dwg"): ("source_file", ".dwg"),
    ("source_intake", "canonical_dxf"): ("derived_dxf", ".dxf"),
    ("drawing_processing", "processed_dxf"): ("processed_drawing", ".dxf"),
    ("windows_cam", "cam_output_dxf"): ("cam_result", ".dxf"),
    ("delivery_archive", "delivery_dxf"): ("delivery_file", ".dxf"),
}
_REVISION_THREE_ONLY = {
    "cam_input_dxf",
    "cam_package_manifest",
    "accepted_dxf",
    "delivery_excel",
    "archive_manifest",
}
_DXF_ARTIFACTS = {
    "canonical_dxf",
    "classified_dxf",
    "processed_dxf",
    "cam_input_dxf",
    "cam_output_dxf",
    "accepted_dxf",
    "delivery_dxf",
}


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


def _workflow_rows(connection: Connection) -> list[Mapping[str, object]]:
    return list(
        connection.execute(
            sa.text(
                """
                SELECT id, config_json
                FROM workflow_runs
                WHERE workflow_type = 'linux_production'
                ORDER BY id
                """
            )
        ).mappings()
    )


def _stage_rows(
    connection: Connection,
    workflow_id: int,
) -> list[Mapping[str, object]]:
    return list(
        connection.execute(
            sa.text(
                """
                SELECT id, stage_code, status, progress
                FROM workflow_stage_runs
                WHERE workflow_run_id = :workflow_id
                ORDER BY id
                """
            ),
            {"workflow_id": workflow_id},
        ).mappings()
    )


def _artifact_rows(
    connection: Connection,
    workflow_id: int,
) -> list[Mapping[str, object]]:
    return list(
        connection.execute(
            sa.text(
                """
                SELECT id, stage_run_id, artifact_type, file_id
                FROM workflow_artifacts
                WHERE workflow_run_id = :workflow_id
                ORDER BY id
                """
            ),
            {"workflow_id": workflow_id},
        ).mappings()
    )


def _input_item_rows(
    connection: Connection,
    workflow_id: int,
) -> list[Mapping[str, object]]:
    return list(
        connection.execute(
            sa.text(
                """
                SELECT item.id, item.file_id, item.status,
                       item.derived_dxf_file_id, item.drawing_id
                FROM workflow_input_items AS item
                JOIN workflow_input_batches AS batch
                  ON batch.id = item.input_batch_id
                WHERE batch.workflow_run_id = :workflow_id
                  AND item.role = 'source_dwg'
                ORDER BY item.id
                """
            ),
            {"workflow_id": workflow_id},
        ).mappings()
    )


def _drawing_version_rows(
    connection: Connection,
    drawing_id: int,
    source: str,
) -> list[Mapping[str, object]]:
    return list(
        connection.execute(
            sa.text(
                """
                SELECT id, file_id
                FROM drawing_versions
                WHERE drawing_id = :drawing_id
                  AND source = :source
                ORDER BY id
                """
            ),
            {"drawing_id": drawing_id, "source": source},
        ).mappings()
    )


def _require_extension(
    connection: Connection,
    *,
    file_id: object,
    expected_extension: str,
    context: str,
) -> int:
    if file_id is None:
        label = "DXF" if expected_extension == ".dxf" else "DWG"
        raise RuntimeError(f"{context} does not reference a {label}")
    row = connection.execute(
        sa.text(
            """
            SELECT file_ext, status
            FROM files
            WHERE id = :file_id
            """
        ),
        {"file_id": int(file_id)},
    ).mappings().one_or_none()
    if (
        row is None
        or str(row["status"]).lower() == "deleted"
        or str(row["file_ext"] or "").lower() != expected_extension
    ):
        label = "DXF" if expected_extension == ".dxf" else "DWG"
        raise RuntimeError(f"{context} does not reference a {label}")
    return int(file_id)


def _serialized_config(config: dict[str, Any]) -> str:
    return json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _revision_config(
    workflow: Mapping[str, object],
    *,
    expected_revision: int,
    target_revision: int,
) -> str:
    workflow_id = int(workflow["id"])
    config = _config_object(workflow["config_json"], workflow_id=workflow_id)
    if config.get("definition_revision") != expected_revision:
        raise RuntimeError(
            f"linux_production workflow {workflow_id} is not definition revision "
            f"{expected_revision}"
        )
    config["definition_revision"] = target_revision
    return _serialized_config(config)


def _upgrade_linux_workflows(connection: Connection) -> None:
    artifact_updates: list[tuple[int, str]] = []
    version_updates: list[tuple[int, int, str]] = []
    config_updates: list[tuple[int, str]] = []

    for workflow in _workflow_rows(connection):
        workflow_id = int(workflow["id"])
        config_updates.append(
            (
                workflow_id,
                _revision_config(
                    workflow,
                    expected_revision=2,
                    target_revision=3,
                ),
            )
        )
        stages = _stage_rows(connection, workflow_id)
        stage_by_id = {int(stage["id"]): stage for stage in stages}
        artifacts = _artifact_rows(connection, workflow_id)

        acceptance = next(
            (
                stage
                for stage in stages
                if stage["stage_code"] == "result_acceptance"
            ),
            None,
        )
        acceptance_artifacts = [
            artifact
            for artifact in artifacts
            if acceptance is not None
            and artifact["stage_run_id"] == acceptance["id"]
        ]
        if acceptance is not None and (
            str(acceptance["status"]) in {"queued", "running", "succeeded", "failed", "cancelled"}
            or int(acceptance["progress"] or 0) > 0
            or acceptance_artifacts
        ):
            raise RuntimeError(
                f"linux_production workflow {workflow_id} cannot infer revision 3 "
                "artifact for progressed result_acceptance"
            )

        for artifact in artifacts:
            artifact_id = int(artifact["id"])
            artifact_type = str(artifact["artifact_type"])
            stage = (
                stage_by_id.get(int(artifact["stage_run_id"]))
                if artifact["stage_run_id"] is not None
                else None
            )
            stage_code = str(stage["stage_code"]) if stage is not None else ""
            if artifact_type == "cam_package":
                raise RuntimeError(
                    f"workflow artifact {artifact_id} cannot infer revision 3 artifact "
                    "from cam_package"
                )
            mapping = _UPGRADE_ARTIFACTS.get((stage_code, artifact_type))
            if mapping is not None:
                target_type, expected_extension = mapping
                _require_extension(
                    connection,
                    file_id=artifact["file_id"],
                    expected_extension=expected_extension,
                    context=f"workflow artifact {artifact_id}",
                )
                artifact_updates.append((artifact_id, target_type))
                continue
            if artifact_type in {
                "source_file",
                "derived_dxf",
                "processed_drawing",
                "cam_result",
                "delivery_file",
            }:
                raise RuntimeError(
                    f"workflow artifact {artifact_id} cannot infer revision 3 artifact "
                    f"from {artifact_type}"
                )
            if artifact_type in _DXF_ARTIFACTS:
                _require_extension(
                    connection,
                    file_id=artifact["file_id"],
                    expected_extension=".dxf",
                    context=f"workflow artifact {artifact_id}",
                )

        items_by_drawing: dict[int, Mapping[str, object]] = {}
        for item in _input_item_rows(connection, workflow_id):
            if str(item["status"]) != "frozen":
                continue
            if item["derived_dxf_file_id"] is None:
                raise RuntimeError(
                    f"workflow input item {item['id']} is missing derived DXF"
                )
            source_file_id = _require_extension(
                connection,
                file_id=item["file_id"],
                expected_extension=".dwg",
                context=f"workflow input item {item['id']} source",
            )
            derived_file_id = _require_extension(
                connection,
                file_id=item["derived_dxf_file_id"],
                expected_extension=".dxf",
                context=f"workflow input item {item['id']} derived file",
            )
            if item["drawing_id"] is None:
                continue
            drawing_id = int(item["drawing_id"])
            if drawing_id in items_by_drawing:
                raise RuntimeError(
                    f"workflow {workflow_id} has multiple frozen inputs for drawing "
                    f"{drawing_id}"
                )
            items_by_drawing[drawing_id] = item
            for version in _drawing_version_rows(
                connection,
                drawing_id,
                "workflow_input_dwg",
            ):
                if int(version["file_id"]) != source_file_id:
                    raise RuntimeError(
                        f"drawing version {version['id']} does not match its source DWG"
                    )
                version_updates.append(
                    (
                        int(version["id"]),
                        derived_file_id,
                        "workflow_input_dxf",
                    )
                )

    for artifact_id, artifact_type in artifact_updates:
        connection.execute(
            sa.text(
                """
                UPDATE workflow_artifacts
                SET artifact_type = :artifact_type
                WHERE id = :artifact_id
                """
            ),
            {"artifact_id": artifact_id, "artifact_type": artifact_type},
        )
    for version_id, file_id, source in version_updates:
        connection.execute(
            sa.text(
                """
                UPDATE drawing_versions
                SET file_id = :file_id, source = :source
                WHERE id = :version_id
                """
            ),
            {"version_id": version_id, "file_id": file_id, "source": source},
        )
    for workflow_id, config_json in config_updates:
        connection.execute(
            sa.text(
                """
                UPDATE workflow_runs
                SET config_json = :config_json
                WHERE id = :workflow_id
                """
            ),
            {"workflow_id": workflow_id, "config_json": config_json},
        )


def _downgrade_linux_workflows(connection: Connection) -> None:
    artifact_updates: list[tuple[int, str]] = []
    version_updates: list[tuple[int, int, str]] = []
    config_updates: list[tuple[int, str]] = []

    for workflow in _workflow_rows(connection):
        workflow_id = int(workflow["id"])
        config_updates.append(
            (
                workflow_id,
                _revision_config(
                    workflow,
                    expected_revision=3,
                    target_revision=2,
                ),
            )
        )
        stages = _stage_rows(connection, workflow_id)
        stage_by_id = {int(stage["id"]): stage for stage in stages}
        artifacts = _artifact_rows(connection, workflow_id)
        unsupported = sorted(
            {
                str(artifact["artifact_type"])
                for artifact in artifacts
                if str(artifact["artifact_type"]) in _REVISION_THREE_ONLY
            }
        )
        if unsupported:
            raise RuntimeError(
                f"linux_production workflow {workflow_id} artifact types "
                f"{unsupported} cannot be represented by revision 2"
            )

        for artifact in artifacts:
            artifact_id = int(artifact["id"])
            artifact_type = str(artifact["artifact_type"])
            stage = (
                stage_by_id.get(int(artifact["stage_run_id"]))
                if artifact["stage_run_id"] is not None
                else None
            )
            stage_code = str(stage["stage_code"]) if stage is not None else ""
            mapping = _DOWNGRADE_ARTIFACTS.get((stage_code, artifact_type))
            if mapping is None:
                continue
            target_type, expected_extension = mapping
            _require_extension(
                connection,
                file_id=artifact["file_id"],
                expected_extension=expected_extension,
                context=f"workflow artifact {artifact_id}",
            )
            artifact_updates.append((artifact_id, target_type))

        for item in _input_item_rows(connection, workflow_id):
            if str(item["status"]) != "frozen" or item["drawing_id"] is None:
                continue
            if item["derived_dxf_file_id"] is None:
                raise RuntimeError(
                    f"workflow input item {item['id']} is missing derived DXF"
                )
            source_file_id = _require_extension(
                connection,
                file_id=item["file_id"],
                expected_extension=".dwg",
                context=f"workflow input item {item['id']} source",
            )
            derived_file_id = _require_extension(
                connection,
                file_id=item["derived_dxf_file_id"],
                expected_extension=".dxf",
                context=f"workflow input item {item['id']} derived file",
            )
            for version in _drawing_version_rows(
                connection,
                int(item["drawing_id"]),
                "workflow_input_dxf",
            ):
                if int(version["file_id"]) != derived_file_id:
                    raise RuntimeError(
                        f"drawing version {version['id']} does not match its canonical DXF"
                    )
                version_updates.append(
                    (
                        int(version["id"]),
                        source_file_id,
                        "workflow_input_dwg",
                    )
                )

    for artifact_id, artifact_type in artifact_updates:
        connection.execute(
            sa.text(
                """
                UPDATE workflow_artifacts
                SET artifact_type = :artifact_type
                WHERE id = :artifact_id
                """
            ),
            {"artifact_id": artifact_id, "artifact_type": artifact_type},
        )
    for version_id, file_id, source in version_updates:
        connection.execute(
            sa.text(
                """
                UPDATE drawing_versions
                SET file_id = :file_id, source = :source
                WHERE id = :version_id
                """
            ),
            {"version_id": version_id, "file_id": file_id, "source": source},
        )
    for workflow_id, config_json in config_updates:
        connection.execute(
            sa.text(
                """
                UPDATE workflow_runs
                SET config_json = :config_json
                WHERE id = :workflow_id
                """
            ),
            {"workflow_id": workflow_id, "config_json": config_json},
        )


def upgrade() -> None:
    _upgrade_linux_workflows(op.get_bind())


def downgrade() -> None:
    _downgrade_linux_workflows(op.get_bind())
