#!/usr/bin/env python3
"""Exercise registered upload, idempotent Job, DXF preview, and outbound ledgers.

Run from ``backend`` so the application package and its selected storage/database
configuration are authoritative. This classified storage probe soft-deletes its files, removes the
queued synthetic Job, and physically removes only the objects it created.
"""

from __future__ import annotations

import json
import os
from io import BytesIO, StringIO
from unittest.mock import patch
from uuid import uuid4

import ezdxf
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select

from app.main import app
from app.modules.files.interface import FileTransfer, StoredFile, get_storage_backend
from app.modules.jobs.interface import Job
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal


def _auth(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/sessions",
        json={
            "username": os.getenv(
                "VERIFY_ADMIN_USERNAME",
                os.getenv("SUPER_ADMIN_USERNAME", "admin"),
            ),
            "password": os.getenv(
                "VERIFY_ADMIN_PASSWORD",
                os.getenv("SUPER_ADMIN_PASSWORD", "SuperAdminPass1"),
            ),
        },
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _dxf_bytes() -> bytes:
    document = ezdxf.new("R2010", setup=True)
    document.modelspace().add_line((0, 0), (120, 80))
    document.modelspace().add_circle((40, 30), 12)
    stream = StringIO()
    document.write(stream)
    return stream.getvalue().encode(document.output_encoding, errors="replace")


def _xlsx_bytes() -> bytes:
    stream = BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["零件号", "规格", "材质"])
    sheet.append(["VERIFY-P-1", "L50x5", "Q235"])
    workbook.save(stream)
    return stream.getvalue()


def _require(response, status_code: int):
    if response.status_code != status_code:
        raise RuntimeError(f"unexpected {response.status_code}: {response.text[:500]}")
    return None if status_code == 204 else response.json()


def main() -> None:
    probe_id = uuid4().hex
    storage = get_storage_backend()
    storage.check_health()
    client = TestClient(app)
    headers = _auth(client)

    excel_key = f"verify-excel-{probe_id}"
    workbook = _xlsx_bytes()
    with patch(
        "app.modules.excel_processing.routes.processing.dispatch_committed_job",
        lambda _db, _job: None,
    ):
        first = _require(
            client.post(
                "/api/v1/excel-final/upload-and-process",
                headers={**headers, "Idempotency-Key": excel_key},
                files={
                    "upload": (
                        f"verify-{probe_id}.xlsx",
                        workbook,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            ),
            202,
        )["data"]
        replay = _require(
            client.post(
                "/api/v1/excel-final/upload-and-process",
                headers={**headers, "Idempotency-Key": excel_key},
                files={
                    "upload": (
                        f"verify-{probe_id}.xlsx",
                        workbook,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            ),
            202,
        )["data"]
    if first["file_id"] != replay["file_id"] or first["job_id"] != replay["job_id"]:
        raise RuntimeError("idempotent Excel replay created duplicate registrations")
    if first["reused"] or not replay["reused"]:
        raise RuntimeError("Excel replay flags do not distinguish creator and reuser")

    dxf_key = f"verify-dxf-{probe_id}"
    uploaded = _require(
        client.post(
            "/api/v1/files",
            headers={**headers, "Idempotency-Key": dxf_key},
            files={"upload": (f"verify-{probe_id}.dxf", _dxf_bytes(), "application/dxf")},
        ),
        201,
    )["data"]
    preview = _require(
        client.get(f"/api/v1/files/{uploaded['id']}/dxf-preview", headers=headers),
        200,
    )["data"]
    content = client.get(preview["content_url"], headers=headers)
    if content.status_code != 200 or not content.content.lower().startswith(b"<?xml"):
        raise RuntimeError("authenticated DXF preview stream failed")
    _require(client.delete(f"/api/v1/files/{uploaded['id']}", headers=headers), 204)
    _require(client.delete(f"/api/v1/files/{first['file_id']}", headers=headers), 204)

    object_locations: list[tuple[str, str]] = []
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(StoredFile).where(
                    StoredFile.id.in_(
                        [uploaded["id"], preview["preview_file_id"], first["file_id"]]
                    )
                )
            )
        )
        object_locations = [(row.bucket, row.storage_key) for row in rows]
        statuses = {row.id: row.status for row in rows}
        transfers = list(
            db.scalars(
                select(FileTransfer).where(
                    FileTransfer.file_id.in_(
                        [uploaded["id"], preview["preview_file_id"], first["file_id"]]
                    )
                )
            )
        )
        operations = {
            (row.direction, row.operation, row.status, row.transferred_bytes)
            for row in transfers
        }
        required = {
            ("inbound", "upload", "succeeded"),
            ("internal", "preview_generate", "succeeded"),
            ("outbound", "preview", "succeeded"),
            ("internal", "preview_invalidate", "succeeded"),
            ("internal", "soft_delete", "succeeded"),
        }
        observed = {(direction, operation, status) for direction, operation, status, _ in operations}
        if not required.issubset(observed):
            raise RuntimeError(f"missing transfer outcomes: {sorted(required - observed)}")
        if statuses.get(uploaded["id"]) != "deleted":
            raise RuntimeError("DXF source was not soft-deleted")
        if statuses.get(preview["preview_file_id"]) != "deleted":
            raise RuntimeError("DXF preview did not follow source lifecycle")
        job = db.get(Job, first["job_id"])
        if job is None or job.request_key != f"upload-and-process:{excel_key}":
            raise RuntimeError("Excel Job request key was not registered")
        db.delete(job)
        db.commit()

    for bucket, storage_key in object_locations:
        storage.delete_object(bucket, storage_key)

    print(
        json.dumps(
            {
                "storage_backend": settings.storage_backend,
                "database_backend": settings.sqlalchemy_database_url.split(":", 1)[0],
                "excel_file_id": first["file_id"],
                "excel_job_id": first["job_id"],
                "excel_replay_reused": replay["reused"],
                "dxf_source_id": uploaded["id"],
                "preview_file_id": preview["preview_file_id"],
                "preview_bytes": len(content.content),
                "registered_objects": len(object_locations),
                "transfer_operations": sorted(
                    f"{direction}:{operation}:{status}:{transferred_bytes}"
                    for direction, operation, status, transferred_bytes in operations
                ),
                "cleanup": "metadata soft-deleted; probe objects removed",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
