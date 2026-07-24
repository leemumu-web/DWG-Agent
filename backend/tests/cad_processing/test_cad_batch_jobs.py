from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import sessionmaker

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.jobs.routes.commands import _job_cancellation_lock_statement
from app.platform.config.settings import settings


@pytest.fixture(autouse=True)
def _enable_conversion_pipelines(monkeypatch):
    monkeypatch.setattr(settings, "dxf_pipeline_enabled", True)
    monkeypatch.setattr(settings, "dxf2dwg_pipeline_enabled", True)


def _admin_headers(client: TestClient) -> dict[str, str]:
    init_db()
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _upload_dwg(client: TestClient, headers: dict[str, str], name: str) -> int:
    response = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": (name, b"AC1027\x00" + b"\x00" * 1024, "application/acad")},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _upload_dxf(client: TestClient, headers: dict[str, str], name: str) -> int:
    payload = (
        b"  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC1027\n"
        b"  0\nENDSEC\n  0\nSECTION\n  2\nENTITIES\n  0\nENDSEC\n  0\nEOF\n"
    )
    response = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": (name, payload, "application/dxf")},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _create_batch(
    client: TestClient,
    headers: dict[str, str],
    *,
    task_type: str,
    file_ids: list[int],
):
    return client.post(
        "/api/v1/workflows/jobs/batches",
        headers=headers,
        json={
            "task_type": task_type,
            "file_ids": file_ids,
            "precision_level": "normal",
        },
    )


def test_create_conversion_batch_returns_ordered_jobs_and_dispatches_once():
    client = TestClient(app)
    headers = _admin_headers(client)
    file_ids = [
        _upload_dwg(client, headers, "first.dwg"),
        _upload_dwg(client, headers, "second.dwg"),
    ]

    with patch(
        "app.modules.jobs.routes.commands.dispatch_committed_conversion_batch",
        create=True,
    ) as dispatch:
        response = _create_batch(
            client,
            headers,
            task_type="convert_dwg_to_dxf",
            file_ids=file_ids,
        )

    assert response.status_code == 202, response.text
    jobs = response.json()["data"]["jobs"]
    assert [job["params_json"]["file_id"] for job in jobs] == file_ids
    assert [job["status"] for job in jobs] == ["queued", "queued"]
    dispatch.assert_called_once_with(
        task_type="convert_dwg_to_dxf",
        jobs=[(job["id"], job["attempt"]) for job in jobs],
    )


def test_create_conversion_batch_rejects_wrong_extension_without_partial_jobs():
    client = TestClient(app)
    headers = _admin_headers(client)
    dwg_id = _upload_dwg(client, headers, "valid.dwg")
    dxf_id = _upload_dxf(client, headers, "wrong.dxf")
    before = client.get("/api/v1/workflows/jobs", headers=headers).json()["pagination"]["total"]

    response = _create_batch(
        client,
        headers,
        task_type="convert_dwg_to_dxf",
        file_ids=[dwg_id, dxf_id],
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_CONVERSION_SOURCE"
    after = client.get("/api/v1/workflows/jobs", headers=headers).json()["pagination"]["total"]
    assert after == before


def test_scoped_cancellation_only_changes_requested_jobs():
    client = TestClient(app)
    headers = _admin_headers(client)
    file_ids = [_upload_dwg(client, headers, f"source-{index}.dwg") for index in range(3)]
    with patch(
        "app.modules.jobs.routes.commands.dispatch_committed_conversion_batch",
        create=True,
    ):
        created = _create_batch(
            client,
            headers,
            task_type="convert_dwg_to_dxf",
            file_ids=file_ids,
        )
    assert created.status_code == 202, created.text
    jobs = created.json()["data"]["jobs"]
    requested_ids = [jobs[0]["id"], jobs[2]["id"]]

    response = client.post(
        "/api/v1/workflows/jobs/cancellation-requests",
        headers=headers,
        json={"job_ids": requested_ids},
    )

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {
        "cancelled_count": 2,
        "cancelled_job_ids": requested_ids,
    }
    states = [
        client.get(f"/api/v1/workflows/jobs/{job['id']}", headers=headers).json()["data"]["status"]
        for job in jobs
    ]
    assert states == ["cancelled", "queued", "cancelled"]


def test_scoped_cancellation_locks_jobs_before_worker_state_can_change():
    sql = str(
        _job_cancellation_lock_statement([3, 7]).compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "jobs.id IN (3, 7)" in sql
    assert sql.endswith(" FOR UPDATE")


def test_conversion_events_stream_returns_ordered_terminal_snapshot():
    client = TestClient(app)
    headers = _admin_headers(client)
    file_ids = [_upload_dwg(client, headers, f"stream-{index}.dwg") for index in range(2)]
    with patch("app.modules.jobs.routes.commands.dispatch_committed_conversion_batch"):
        created = _create_batch(
            client,
            headers,
            task_type="convert_dwg_to_dxf",
            file_ids=file_ids,
        )
    jobs = created.json()["data"]["jobs"]
    cancelled = client.post(
        "/api/v1/workflows/jobs/cancellation-requests",
        headers=headers,
        json={"job_ids": [job["id"] for job in jobs]},
    )
    assert cancelled.status_code == 202, cancelled.text

    response = client.get(
        "/api/v1/workflows/jobs/events/stream",
        headers=headers,
        params={
            "task_type": "convert_dwg_to_dxf",
            "file_ids": ",".join(str(file_id) for file_id in reversed(file_ids)),
        },
    )

    assert response.status_code == 200, response.text
    data_line = next(line for line in response.text.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["type"] == "snapshot"
    assert [job["params_json"]["file_id"] for job in payload["jobs"]] == list(reversed(file_ids))
    assert [job["status"] for job in payload["jobs"]] == ["cancelled", "cancelled"]


def test_conversion_events_stream_rejects_more_than_200_files():
    client = TestClient(app)
    headers = _admin_headers(client)

    response = client.get(
        "/api/v1/workflows/jobs/events/stream",
        headers=headers,
        params={
            "task_type": "convert_dwg_to_dxf",
            "file_ids": ",".join(str(index) for index in range(1, 202)),
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_PARAMS"


def test_list_jobs_latest_per_file_omits_superseded_attempt_rows():
    client = TestClient(app)
    headers = _admin_headers(client)
    file_id = _upload_dwg(client, headers, "latest-only.dwg")
    with patch("app.modules.jobs.routes.commands.dispatch_committed_conversion_batch"):
        first = _create_batch(
            client,
            headers,
            task_type="convert_dwg_to_dxf",
            file_ids=[file_id],
        ).json()["data"]["jobs"][0]
        second = _create_batch(
            client,
            headers,
            task_type="convert_dwg_to_dxf",
            file_ids=[file_id],
        ).json()["data"]["jobs"][0]

    response = client.get(
        "/api/v1/workflows/jobs",
        headers=headers,
        params={
            "task_type": "convert_dwg_to_dxf",
            "file_ids": str(file_id),
            "latest_per_file": "true",
            "page_size": 200,
        },
    )

    assert response.status_code == 200, response.text
    assert [job["id"] for job in response.json()["data"]] == [second["id"]]
    assert first["id"] != second["id"]


def test_oda_batch_group_uses_bounded_parallel_shards(tmp_path, monkeypatch):
    from dwg_converter.engines.oda_converter import BatchResult, ConvertResult

    from app.modules.cad_processing import batching as cad_batch_service

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    files = []
    for index in range(10):
        source = input_dir / f"job-{index}.dwg"
        source.write_bytes(b"AC1027")
        files.append(source)
    calls: list[list[str]] = []

    def fake_convert_directory(source_dir, target_dir, **_kwargs):
        target_dir.mkdir(parents=True, exist_ok=True)
        names = sorted(path.name for path in source_dir.glob("*.dwg"))
        calls.append(names)
        results = []
        for name in names:
            source = source_dir / name
            target = target_dir / f"{source.stem}.dxf"
            target.write_text("SECTION")
            results.append(ConvertResult(source, target, True))
        return BatchResult(results)

    monkeypatch.setattr(settings, "cad_batch_max_shards", 4, raising=False)
    monkeypatch.setattr(settings, "cad_batch_min_files_per_shard", 2, raising=False)

    results = cad_batch_service._convert_oda_group(
        staged_paths=files,
        output_root=tmp_path / "outputs",
        convert_directory=fake_convert_directory,
        converter_kwargs={"version": "ACAD2018"},
    )

    assert len(calls) == 4
    assert sorted(name for call in calls for name in call) == [path.name for path in files]
    assert len(results) == len(files)


def test_dwg_batch_groups_same_version_into_one_oda_call_and_completes_each_job(db, monkeypatch):
    from dwg_converter.engines.oda_converter import BatchResult, ConvertResult

    from app.modules.cad_processing.dwg_to_dxf import batch as cad_batch_service

    client = TestClient(app)
    headers = _admin_headers(client)
    file_ids = [
        _upload_dwg(client, headers, "batch-first.dwg"),
        _upload_dwg(client, headers, "batch-second.dwg"),
    ]
    with patch("app.modules.jobs.routes.commands.dispatch_committed_conversion_batch"):
        created = _create_batch(
            client,
            headers,
            task_type="convert_dwg_to_dxf",
            file_ids=file_ids,
        )
    jobs = created.json()["data"]["jobs"]
    test_sessions = sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(cad_batch_service, "SessionLocal", test_sessions)

    calls: list[dict[str, object]] = []

    def fake_convert_directory(source_dir, target_dir, **kwargs):
        calls.append({"source_dir": source_dir, "target_dir": target_dir, **kwargs})
        target_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for source in sorted(source_dir.glob("*.dwg")):
            target = target_dir / f"{source.stem}.dxf"
            target.write_text(
                "  0\nSECTION\n  2\nENTITIES\n  0\nENDSEC\n  0\nEOF\n",
                encoding="ascii",
            )
            results.append(
                ConvertResult(
                    source=source,
                    target=target,
                    success=True,
                    returncode=0,
                    duration=0.25,
                )
            )
        return BatchResult(results)

    monkeypatch.setattr("dwg_converter.convert_directory", fake_convert_directory)
    summary = cad_batch_service.run_dwg_to_dxf_batch(
        [(job["id"], job["attempt"]) for job in jobs],
        worker_name="batch-test",
    )

    assert summary == {"total": 2, "succeeded": 2, "failed": 0, "skipped": 0}
    assert len(calls) == 1
    assert calls[0]["version"] == "ACAD2013"
    for job in jobs:
        current = client.get(f"/api/v1/workflows/jobs/{job['id']}", headers=headers).json()["data"]
        assert current["status"] == "succeeded"
        assert current["progress"] == 100
        results = client.get(f"/api/v1/workflows/jobs/{job['id']}/results", headers=headers).json()["data"]
        assert len(results) == 1
        assert results[0]["result_type"] == "convert_dwg_to_dxf"


def test_dxf_batch_groups_same_version_into_one_oda_call_and_completes_each_job(db, monkeypatch):
    from dxf_converter.engines.oda_converter import BatchResult, ConvertResult

    from app.modules.cad_processing.dxf_to_dwg import batch as cad_batch_service

    client = TestClient(app)
    headers = _admin_headers(client)
    file_ids = [
        _upload_dxf(client, headers, "batch-first.dxf"),
        _upload_dxf(client, headers, "batch-second.dxf"),
    ]
    with patch("app.modules.jobs.routes.commands.dispatch_committed_conversion_batch"):
        created = _create_batch(
            client,
            headers,
            task_type="convert_dxf_to_dwg",
            file_ids=file_ids,
        )
    jobs = created.json()["data"]["jobs"]
    test_sessions = sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(cad_batch_service, "SessionLocal", test_sessions)

    calls: list[dict[str, object]] = []

    def fake_convert_directory(source_dir, target_dir, **kwargs):
        calls.append({"source_dir": source_dir, "target_dir": target_dir, **kwargs})
        target_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for source in sorted(source_dir.glob("*.dxf")):
            target = target_dir / f"{source.stem}.dwg"
            target.write_bytes(b"AC1027\x00" + b"\x00" * 1024)
            results.append(
                ConvertResult(
                    source=source,
                    target=target,
                    success=True,
                    returncode=0,
                    duration=0.25,
                )
            )
        return BatchResult(results)

    monkeypatch.setattr("dxf_converter.convert_directory", fake_convert_directory)
    summary = cad_batch_service.run_dxf_to_dwg_batch(
        [(job["id"], job["attempt"]) for job in jobs],
        worker_name="batch-test",
    )

    assert summary == {"total": 2, "succeeded": 2, "failed": 0, "skipped": 0}
    assert len(calls) == 1
    assert calls[0]["version"] == "ACAD2013"
    for job in jobs:
        current = client.get(f"/api/v1/workflows/jobs/{job['id']}", headers=headers).json()["data"]
        assert current["status"] == "succeeded"
        assert current["progress"] == 100
        results = client.get(f"/api/v1/workflows/jobs/{job['id']}/results", headers=headers).json()["data"]
        assert len(results) == 1
        assert results[0]["result_type"] == "convert_dxf_to_dwg"


def test_dwg_batch_missing_result_fails_only_the_unmatched_job(db, monkeypatch):
    from dwg_converter.engines.oda_converter import BatchResult, ConvertResult

    from app.modules.cad_processing.dwg_to_dxf import batch as cad_batch_service

    client = TestClient(app)
    headers = _admin_headers(client)
    file_ids = [
        _upload_dwg(client, headers, "matched.dwg"),
        _upload_dwg(client, headers, "missing.dwg"),
    ]
    with patch("app.modules.jobs.routes.commands.dispatch_committed_conversion_batch"):
        created = _create_batch(
            client,
            headers,
            task_type="convert_dwg_to_dxf",
            file_ids=file_ids,
        )
    jobs = created.json()["data"]["jobs"]
    test_sessions = sessionmaker(
        bind=db.get_bind(), autoflush=False, autocommit=False, expire_on_commit=False
    )
    monkeypatch.setattr(cad_batch_service, "SessionLocal", test_sessions)

    def fake_partial_batch(source_dir, target_dir, **_kwargs):
        first = sorted(source_dir.glob("*.dwg"))[0]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{first.stem}.dxf"
        target.write_text("  0\nEOF\n", encoding="ascii")
        return BatchResult([ConvertResult(source=first, target=target, success=True, returncode=0)])

    monkeypatch.setattr("dwg_converter.convert_directory", fake_partial_batch)
    summary = cad_batch_service.run_dwg_to_dxf_batch(
        [(job["id"], job["attempt"]) for job in jobs], worker_name="batch-test"
    )

    states = [
        client.get(f"/api/v1/workflows/jobs/{job['id']}", headers=headers).json()["data"]["status"]
        for job in jobs
    ]
    assert summary == {"total": 2, "succeeded": 1, "failed": 1, "skipped": 0}
    assert states == ["succeeded", "failed"]
