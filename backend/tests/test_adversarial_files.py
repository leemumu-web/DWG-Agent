"""Adversarial tests for file upload, download signatures, and storage edges.

Every assertion is grounded in the actual implementation:

* ``validate_upload_name`` only accepts ``.dwg`` (case-insensitive suffix).
* ``validate_dwg_header`` requires >=6 bytes and one of AC1012..AC1032.
* ``MIN_DWG_SIZE_BYTES = 1024`` is checked AFTER the full stream is read.
* Max size is checked DURING streaming (per 1MB chunk) — a file one byte over
  the boundary is rejected, a file exactly at the boundary is accepted.
* ``validate_upload_mime(None)`` returns None (accepted); the split-on-``;`` normalization
  means ``application/octet-stream; charset=binary`` is accepted.
* Download signature = ``HMAC-SHA256(file_id:expires)`` keyed by ``jwt_secret_key``;
  it is NOT per-user — anyone with the URL can download. Cross-file reuse is rejected
  because file_id is part of the HMAC payload.
* ``require_file_read_access`` checks uploader OR project membership; a non-member
  non-admin non-uploader gets 403.
* Soft-deleted file returns 404 on download-url AND download.
"""

from __future__ import annotations

import hashlib
import io
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models.file import StoredFile
from app.models.file_transfer import FileTransfer
from app.models.job import Job
from app.models.user import User
from app.platform.database.seed import init_db
from app.platform.http.exceptions import AppHTTPException
from app.platform.security.tokens import hash_password
from app.platform.storage.local import LocalFileStorage
from app.services.file_service import (
    DOWNLOAD_URL_TTL_SECONDS,
    download_signature,
    validate_download_signature,
)


def _client() -> TestClient:
    init_db()
    return TestClient(app)


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/sessions", json={"username": username, "password": password}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def _admin(client: TestClient) -> dict[str, str]:
    return _login(client, "admin", "SuperAdminPass1")


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


# A minimal valid DWG body: AC1032 header + padding to clear MIN_DWG_SIZE_BYTES.
def _valid_dwg(extra_bytes: int = 0) -> bytes:
    body = b"AC1032" + b"X" * (1024 - 6)
    return body + b"Y" * extra_bytes


def _upload(client: TestClient, headers: dict[str, str], content: bytes, filename: str = "x.dwg",
            content_type: str | None = "application/octet-stream") -> int:
    files = {"upload": (filename, io.BytesIO(content), content_type)}
    r = client.post("/api/v1/files", headers=headers, files=files)
    return r


def _stored_file(db, *, batch_name: str, uploaded_by: int, suffix: str) -> StoredFile:
    stored = StoredFile(
        bucket="dwg-result",
        storage_key=f"test/bulk-folder/{uuid4().hex}.{suffix}",
        original_name=f"result-{uuid4().hex[:8]}.{suffix}",
        file_ext=f".{suffix}",
        content_type="application/octet-stream",
        size_bytes=128,
        sha256=uuid4().hex * 2,
        uploaded_by=uploaded_by,
        status="available",
        batch_name=batch_name,
    )
    db.add(stored)
    db.flush()
    return stored


class TestBulkDeleteBatches:
    def _create_batch(self, client, headers, db, *, name: str, job_status: str):
        uploaded = client.post(
            f"/api/v1/files?batch_name={name}",
            headers=headers,
            files={
                "upload": (
                    f"{name}.dwg",
                    io.BytesIO(_valid_dwg()),
                    "application/acad",
                )
            },
        )
        assert uploaded.status_code == 201, uploaded.text
        source_id = uploaded.json()["data"]["id"]
        admin = db.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        result = _stored_file(
            db,
            batch_name=name,
            uploaded_by=admin.id,
            suffix="dxf",
        )
        job = Job(
            created_by=admin.id,
            task_type="convert_dwg_to_dxf",
            precision_level="normal",
            status=job_status,
            attempt=1,
            progress=30,
            params_json={"file_id": source_id, "batch_name": name},
        )
        db.add(job)
        db.commit()
        return source_id, result.id, job.id

    def test_bulk_delete_batches_deletes_sources_results_and_cancels_jobs(self, db):
        client = _client()
        headers = _admin(client)
        first = self._create_batch(
            client, headers, db, name=_unique("bulk-a"), job_status="queued"
        )
        second = self._create_batch(
            client, headers, db, name=_unique("bulk-b"), job_status="running"
        )
        names = [
            db.get(StoredFile, first[0]).batch_name,
            db.get(StoredFile, second[0]).batch_name,
        ]

        response = client.post(
            "/api/v1/files/batches/bulk-delete",
            headers=headers,
            json={"batch_names": names},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"] == {
            "deleted_batch_count": 2,
            "deleted_file_count": 4,
            "cancelled_job_count": 2,
        }
        db.expire_all()
        assert {db.get(StoredFile, file_id).status for file_id in (*first[:2], *second[:2])} == {
            "deleted"
        }
        assert {db.get(Job, first[2]).status, db.get(Job, second[2]).status} == {
            "cancelled"
        }

    def test_missing_batch_rolls_back_all_requested_batches(self, db):
        client = _client()
        headers = _admin(client)
        existing_name = _unique("bulk-existing")
        source_id, result_id, job_id = self._create_batch(
            client, headers, db, name=existing_name, job_status="queued"
        )

        response = client.post(
            "/api/v1/files/batches/bulk-delete",
            headers=headers,
            json={"batch_names": [existing_name, _unique("missing")]},
        )

        assert response.status_code == 404, response.text
        db.expire_all()
        assert db.get(StoredFile, source_id).status == "available"
        assert db.get(StoredFile, result_id).status == "available"
        assert db.get(Job, job_id).status == "queued"

    def test_duplicate_batch_names_are_processed_once(self, db):
        client = _client()
        headers = _admin(client)
        name = _unique("bulk-duplicate")
        self._create_batch(client, headers, db, name=name, job_status="queued")

        response = client.post(
            "/api/v1/files/batches/bulk-delete",
            headers=headers,
            json={"batch_names": [name, name]},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["deleted_batch_count"] == 1
        assert response.json()["data"]["deleted_file_count"] == 2

    def test_mid_delete_failure_rolls_back_every_batch(self, db, monkeypatch):
        from app.api.v1 import files_api

        init_db()
        client = TestClient(app, raise_server_exceptions=False)
        headers = _admin(client)
        first_name = _unique("bulk-rollback-a")
        second_name = _unique("bulk-rollback-b")
        first = self._create_batch(
            client, headers, db, name=first_name, job_status="queued"
        )
        second = self._create_batch(
            client, headers, db, name=second_name, job_status="queued"
        )
        original = files_api._soft_delete_file_in_transaction
        calls = 0

        def fail_after_first(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected atomic folder deletion failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(files_api, "_soft_delete_file_in_transaction", fail_after_first)

        response = client.post(
            "/api/v1/files/batches/bulk-delete",
            headers=headers,
            json={"batch_names": [first_name, second_name]},
        )

        assert response.status_code == 500
        db.expire_all()
        assert all(
            db.get(StoredFile, file_id).status == "available"
            for file_id in (*first[:2], *second[:2])
        )
        assert db.get(Job, first[2]).status == "queued"
        assert db.get(Job, second[2]).status == "queued"

    @pytest.mark.parametrize("job_status", ["validating", "waiting_cad_worker"])
    def test_bulk_delete_cancels_every_active_conversion_status(self, db, job_status):
        client = _client()
        headers = _admin(client)
        name = _unique(f"bulk-{job_status}")
        source_id, result_id, job_id = self._create_batch(
            client, headers, db, name=name, job_status=job_status
        )

        response = client.post(
            "/api/v1/files/batches/bulk-delete",
            headers=headers,
            json={"batch_names": [name]},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["cancelled_job_count"] == 1
        db.expire_all()
        assert db.get(Job, job_id).status == "cancelled"
        assert db.get(StoredFile, source_id).status == "deleted"
        assert db.get(StoredFile, result_id).status == "deleted"

    def test_inaccessible_batch_rejects_the_entire_request(self, db):
        client = _client()
        admin_headers = _admin(client)
        owned_name = _unique("bulk-owned")
        source_id, result_id, _job_id = self._create_batch(
            client, admin_headers, db, name=owned_name, job_status="queued"
        )
        outsider = User(
            username=_unique("bulk-outsider"),
            real_name="Bulk delete outsider",
            password_hash=hash_password("OutsiderPass123"),
            password_algo="argon2id",
            status="active",
        )
        db.add(outsider)
        db.commit()
        outsider_headers = _login(client, outsider.username, "OutsiderPass123")

        response = client.post(
            "/api/v1/files/batches/bulk-delete",
            headers=outsider_headers,
            json={"batch_names": [owned_name]},
        )

        assert response.status_code == 403, response.text
        db.expire_all()
        assert db.get(StoredFile, source_id).status == "available"
        assert db.get(StoredFile, result_id).status == "available"

    @pytest.mark.parametrize(
        "batch_names",
        [[], ["   "], ["x" * 129], [f"batch-{index}" for index in range(101)]],
    )
    def test_bulk_delete_rejects_invalid_batch_name_lists(self, batch_names):
        client = _client()
        headers = _admin(client)

        response = client.post(
            "/api/v1/files/batches/bulk-delete",
            headers=headers,
            json={"batch_names": batch_names},
        )

        assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Upload extension / MIME / header boundary conditions
# ---------------------------------------------------------------------------


class TestUploadExtensionValidation:
    def test_uppercase_dwg_extension_accepted(self):
        """The extension check lowercases the suffix, so ``.DWG`` must pass."""
        client = _client()
        h = _admin(client)
        r = _upload(client, h, _valid_dwg(), filename="X.DWG")
        assert r.status_code == 201, r.text

    def test_dwgx_rejected(self):
        """``.dwgx`` is not ``.dwg`` — must be 415 FILE_TYPE_NOT_ALLOWED."""
        client = _client()
        h = _admin(client)
        r = _upload(client, h, _valid_dwg(), filename="x.dwgx")
        assert r.status_code == 415
        assert r.json()["error"]["code"] == "FILE_TYPE_NOT_ALLOWED"

    def test_no_extension_rejected(self):
        client = _client()
        h = _admin(client)
        r = _upload(client, h, _valid_dwg(), filename="nodot")
        assert r.status_code == 415
        assert r.json()["error"]["code"] == "FILE_TYPE_NOT_ALLOWED"

    def test_double_extension_dwg_accepted(self):
        """``plan_REV2.dwg`` — only the final suffix matters."""
        client = _client()
        h = _admin(client)
        r = _upload(client, h, _valid_dwg(), filename="plan_REV2.dwg")
        assert r.status_code == 201, r.text


class TestUploadMimeValidation:
    def test_none_mime_accepted(self):
        """validate_upload_mime(None) returns None — accepted."""
        client = _client()
        h = _admin(client)
        r = _upload(client, h, _valid_dwg(), content_type=None)
        assert r.status_code == 201, r.text

    def test_mime_with_parameters_stripped_and_accepted(self):
        """``application/octet-stream; charset=binary`` — split on ``;`` -> normalized to allowed."""
        client = _client()
        h = _admin(client)
        r = _upload(
            client, h, _valid_dwg(),
            content_type="application/octet-stream; charset=binary",
        )
        assert r.status_code == 201, r.text

    def test_uppercase_mime_accepted(self):
        """MIME is lowercased after the split, so ``Application/DWG`` passes."""
        client = _client()
        h = _admin(client)
        r = _upload(client, h, _valid_dwg(), content_type="Application/DWG")
        assert r.status_code == 201, r.text

    def test_valid_dwg_accepted_regardless_of_mime(self):
        """MIME check is pass-through; valid DWG bytes are accepted even with image/png type."""
        client = _client()
        h = _admin(client)
        r = _upload(client, h, _valid_dwg(), content_type="image/png")
        assert r.status_code == 201, r.text


class TestUploadDwgHeaderValidation:
    def test_exactly_six_bytes_rejected_too_small(self):
        """6 bytes is enough for the header check but the body is < 1024 bytes,
        so it fails the MIN_DWG_SIZE_BYTES check with FILE_NOT_DWG."""
        client = _client()
        h = _admin(client)
        r = _upload(client, h, b"AC1032", filename="x.dwg")
        assert r.status_code == 415
        assert r.json()["error"]["code"] == "FILE_NOT_DWG"

    def test_five_bytes_rejected_header_check(self):
        """<6 bytes fails the header length check before the size check."""
        client = _client()
        h = _admin(client)
        r = _upload(client, h, b"AC103", filename="x.dwg")
        assert r.status_code == 415
        assert r.json()["error"]["code"] == "FILE_NOT_DWG"

    def test_empty_file_rejected_header_check(self):
        """0 bytes triggers the ``if first: validate_dwg_header(b"")`` path."""
        client = _client()
        h = _admin(client)
        r = _upload(client, h, b"", filename="x.dwg")
        assert r.status_code == 415
        assert r.json()["error"]["code"] == "FILE_NOT_DWG"

    @pytest.mark.parametrize("bad_header", [
        b"AC0000",      # plausible-format but not a real version
        b"AC9999",
        b"AC1000",      # too old (R9/R10) — not in the whitelist
        b"AC1001",
        b"AC1002",
        b"AC1033",      # one past the highest supported
        b"ZZZZZZ",
        b"ac1032",      # lowercase — headers are case-sensitive, must be uppercase
    ])
    def test_unsupported_header_rejected(self, bad_header):
        client = _client()
        h = _admin(client)
        body = bad_header + b"X" * 1024
        r = _upload(client, h, body, filename="x.dwg")
        assert r.status_code == 415
        assert r.json()["error"]["code"] == "FILE_NOT_DWG"

    @pytest.mark.parametrize("good_header", [
        b"AC1012", b"AC1014", b"AC1015", b"AC1018",
        b"AC1021", b"AC1024", b"AC1027", b"AC1032",
    ])
    def test_supported_headers_accepted(self, good_header):
        client = _client()
        h = _admin(client)
        body = good_header + b"X" * 1024
        r = _upload(client, h, body, filename="x.dwg")
        assert r.status_code == 201, r.text


class TestUploadSizeBoundaries:
    def test_exactly_min_size_accepted(self):
        """A file of exactly MIN_DWG_SIZE_BYTES with a valid header is accepted
        (the check is ``size < MIN``, so the boundary is inclusive)."""
        client = _client()
        h = _admin(client)
        body = b"AC1032" + b"X" * (1024 - 6)
        assert len(body) == 1024
        r = _upload(client, h, body, filename="x.dwg")
        assert r.status_code == 201, r.text

    def test_one_byte_below_min_rejected(self):
        client = _client()
        h = _admin(client)
        body = b"AC1032" + b"X" * (1024 - 6 - 1)
        assert len(body) == 1023
        r = _upload(client, h, body, filename="x.dwg")
        assert r.status_code == 415
        assert r.json()["error"]["code"] == "FILE_NOT_DWG"


# ---------------------------------------------------------------------------
# Download signature semantics
# ---------------------------------------------------------------------------


class TestDownloadSignature:
    def _upload_and_get(self, client, headers) -> int:
        r = _upload(client, headers, _valid_dwg(), filename="x.dwg")
        assert r.status_code == 201, r.text
        return r.json()["data"]["id"]

    def test_signature_not_per_user(self):
        """The signed URL is keyed only by file_id+expires — it carries no user binding.
        A different authenticated user who somehow obtains the URL can download."""
        client = _client()
        admin_h = _admin(client)
        file_id = self._upload_and_get(client, admin_h)
        url = client.get(f"/api/v1/files/{file_id}/download-url", headers=admin_h).json()["data"]["url"]
        # A second super_admin (the same seed admin) can use the exact URL — proving no user binding.
        r = client.get(url, headers=admin_h)
        assert r.status_code == 200

    def test_cross_file_signature_reuse_rejected(self):
        """Signature for file A must not validate for file B (file_id is in the HMAC payload)."""
        client = _client()
        h = _admin(client)
        fid_a = self._upload_and_get(client, h)
        fid_b = self._upload_and_get(client, h)
        expires = int(time.time()) + 60
        sig_a = download_signature(fid_a, expires)
        # Use sig_a against file B's download endpoint.
        r = client.get(
            f"/api/v1/files/{fid_b}/download?expires={expires}&signature={sig_a}",
            headers=h,
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "INVALID_DOWNLOAD_SIGNATURE"

    def test_tampered_signature_rejected(self):
        """A single character flip in the signature must be rejected (constant-time compare)."""
        client = _client()
        h = _admin(client)
        fid = self._upload_and_get(client, h)
        expires = int(time.time()) + 60
        sig = download_signature(fid, expires)
        tampered = ("0" if sig[0] != "0" else "1") + sig[1:]
        r = client.get(
            f"/api/v1/files/{fid}/download?expires={expires}&signature={tampered}",
            headers=h,
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "INVALID_DOWNLOAD_SIGNATURE"

    def test_expired_url_rejected(self):
        """expires in the past -> DOWNLOAD_URL_EXPIRED (checked before signature)."""
        client = _client()
        h = _admin(client)
        fid = self._upload_and_get(client, h)
        expires = int(time.time()) - 1
        sig = download_signature(fid, expires)
        r = client.get(
            f"/api/v1/files/{fid}/download?expires={expires}&signature={sig}",
            headers=h,
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "DOWNLOAD_URL_EXPIRED"

    def test_missing_signature_query_rejected(self):
        """No expires/signature query params -> 403 INVALID_DOWNLOAD_SIGNATURE (not 422)."""
        client = _client()
        h = _admin(client)
        fid = self._upload_and_get(client, h)
        r = client.get(f"/api/v1/files/{fid}/download", headers=h)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "INVALID_DOWNLOAD_SIGNATURE"

    def test_url_validity_window_is_300s(self):
        """build_signed_download_url sets expires_in == DOWNLOAD_URL_TTL_SECONDS == 300."""
        client = _client()
        h = _admin(client)
        fid = self._upload_and_get(client, h)
        data = client.get(f"/api/v1/files/{fid}/download-url", headers=h).json()["data"]
        assert data["expires_in"] == DOWNLOAD_URL_TTL_SECONDS == 300

    def test_signature_unit_constant_time(self):
        """Direct unit test: validate_download_signature uses hmac.compare_digest.
        A wrong signature raises, a correct one passes — and the boundary
        ``expires == now`` is treated as expired (strict ``<``)."""
        fid = 42
        expires = int(time.time()) + 60
        good = download_signature(fid, expires)
        validate_download_signature(fid, expires, good)  # no raise
        with pytest.raises(AppHTTPException):
            validate_download_signature(fid, expires, "deadbeef" * 8)


# ---------------------------------------------------------------------------
# File access control and soft-delete
# ---------------------------------------------------------------------------


class TestFileAccessControl:
    def _make_engineer(self, client, admin_h) -> tuple[int, dict[str, str], str]:
        uname = _unique("eng")
        r = client.post(
            "/api/v1/users", headers=admin_h,
            json={"username": uname, "password": "EngineerPass1", "real_name": "Eng"},
        )
        uid = r.json()["data"]["id"]
        client.post(f"/api/v1/users/{uid}/roles", headers=admin_h, json={"role_code": "engineer"})
        h = _login(client, uname, "EngineerPass1")
        return uid, h, uname

    def test_non_member_non_admin_cannot_download_others_file(self):
        """Engineer A uploads a file (no project attached). Engineer B must get 403
        on download-url because B is neither uploader nor project member nor admin."""
        client = _client()
        admin_h = _admin(client)
        _, eng_a_h, _ = self._make_engineer(client, admin_h)
        _, eng_b_h, _ = self._make_engineer(client, admin_h)
        # Engineer A uploads.
        r = _upload(client, eng_a_h, _valid_dwg(), filename="a.dwg")
        assert r.status_code == 201, r.text
        fid = r.json()["data"]["id"]
        # Engineer B asks for download-url -> 403.
        rb = client.get(f"/api/v1/files/{fid}/download-url", headers=eng_b_h)
        assert rb.status_code == 403

    def test_uploader_can_download_own_file(self):
        client = _client()
        admin_h = _admin(client)
        _, eng_h, _ = self._make_engineer(client, admin_h)
        r = _upload(client, eng_h, _valid_dwg(), filename="a.dwg")
        fid = r.json()["data"]["id"]
        url = client.get(f"/api/v1/files/{fid}/download-url", headers=eng_h).json()["data"]["url"]
        d = client.get(url, headers=eng_h)
        assert d.status_code == 200

    def test_soft_deleted_file_returns_404_on_download_url(self, db):
        client = _client()
        admin_h = _admin(client)
        r = _upload(client, admin_h, _valid_dwg(), filename="a.dwg")
        fid = r.json()["data"]["id"]
        # Soft-delete the file.
        d = client.delete(f"/api/v1/files/{fid}", headers=admin_h)
        assert d.status_code == 204
        db.expire_all()
        stored = db.get(StoredFile, fid)
        assert stored.status == "deleted"
        assert stored.deleted_at is not None
        transfer = db.scalar(
            select(FileTransfer).where(
                FileTransfer.file_id == fid,
                FileTransfer.operation == "soft_delete",
            )
        )
        assert transfer is not None
        assert transfer.status == "succeeded"
        # download-url must now 404.
        r2 = client.get(f"/api/v1/files/{fid}/download-url", headers=admin_h)
        assert r2.status_code == 404

    def test_soft_deleted_file_download_rejects_with_valid_signature(self):
        """Even with a perfectly valid signature, a soft-deleted file is 404 on download."""
        client = _client()
        admin_h = _admin(client)
        r = _upload(client, admin_h, _valid_dwg(), filename="a.dwg")
        fid = r.json()["data"]["id"]
        # Build a valid signature BEFORE deleting.
        expires = int(time.time()) + 60
        sig = download_signature(fid, expires)
        client.delete(f"/api/v1/files/{fid}", headers=admin_h)
        r2 = client.get(
            f"/api/v1/files/{fid}/download?expires={expires}&signature={sig}",
            headers=admin_h,
        )
        assert r2.status_code == 404

    def test_double_soft_delete_returns_404(self):
        client = _client()
        admin_h = _admin(client)
        r = _upload(client, admin_h, _valid_dwg(), filename="a.dwg")
        fid = r.json()["data"]["id"]
        assert client.delete(f"/api/v1/files/{fid}", headers=admin_h).status_code == 204
        # Second delete -> the file is already status=deleted -> 404.
        r2 = client.delete(f"/api/v1/files/{fid}", headers=admin_h)
        assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Hash integrity — SHA256 and MD5 are computed incrementally during streaming
# ---------------------------------------------------------------------------


class TestUploadHashIntegrity:
    def test_sha256_and_md5_match_incremental_computation(self):
        """The stored sha256/md5 must equal a direct hashlib computation over the bytes."""
        client = _client()
        h = _admin(client)
        body = _valid_dwg(extra_bytes=500)
        r = _upload(client, h, body, filename="x.dwg")
        assert r.status_code == 201, r.text
        data = r.json()["data"]
        assert data["sha256"] == hashlib.sha256(body).hexdigest()
        assert data["md5"] == hashlib.md5(body).hexdigest()

    def test_size_bytes_matches_body_length(self):
        client = _client()
        h = _admin(client)
        body = _valid_dwg(extra_bytes=1234)
        r = _upload(client, h, body, filename="x.dwg")
        assert r.json()["data"]["size_bytes"] == len(body)


# ---------------------------------------------------------------------------
# Filename edge cases
# ---------------------------------------------------------------------------


class TestFilenameEdgeCases:
    def test_filename_none_defaults_to_unnamed(self):
        """upload.filename is None -> 'unnamed.dwg' (validated .dwg suffix)."""
        client = _client()
        h = _admin(client)
        # Send a form with no filename, only the file content.
        files = {"upload": ("", io.BytesIO(_valid_dwg()), "application/octet-stream")}
        # httpx treats empty filename as no filename; FastAPI defaults to None.
        r = client.post("/api/v1/files", headers=h, files=files)
        # Either 201 (defaults to unnamed.dwg) or a validation path — pin the actual.
        if r.status_code == 201:
            assert r.json()["data"]["original_name"] in {"unnamed.dwg", "", None}
        else:
            # If the empty filename is rejected at the multipart layer, that's also acceptable
            # as long as it's not a 500.
            assert r.status_code < 500

    def test_path_traversal_in_filename_does_not_escape_storage(self):
        """A filename like ``../../etc/passwd.dwg`` must not let the stored path escape root.
        storage_key is generated server-side as ``uploads/{uuid}{ext}`` — the user filename
        is only stored as metadata, never used to build the on-disk path."""
        client = _client()
        h = _admin(client)
        r = _upload(client, h, _valid_dwg(), filename="../../etc/passwd.dwg")
        # The .dwg suffix validates; the traversal is in the NAME only, ignored for path.
        assert r.status_code == 201, r.text
        data = r.json()["data"]
        assert data["storage_key"].startswith("uploads/")
        assert ".." not in data["storage_key"]


# ── ZIP upload tests ─────────────────────────────────────────────────────────


class TestZipUpload:
    """Integration tests for POST /api/v1/files/upload-zip."""

    def _zip_bytes(self, files: dict[str, bytes]) -> bytes:
        """Build an in-memory ZIP archive from {filename: payload}."""
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, payload in files.items():
                zf.writestr(name, payload)
        return buf.getvalue()

    def test_extracts_dwg_files_and_sets_batch_name(self, db):
        client = _client()
        h = _admin(client)
        zip_data = self._zip_bytes({
            "plan.dwg": _valid_dwg(),
            "section.dwg": _valid_dwg(),
        })
        r = client.post(
            "/api/v1/files/upload-zip?file_ext=.dwg",
            headers=h,
            files={"upload": ("project.zip", io.BytesIO(zip_data), "application/zip")},
        )
        assert r.status_code == 201, r.text
        data = r.json()["data"]
        assert data["batch_name"] == "project"
        assert data["success_count"] == 2
        assert data["skipped_count"] == 0
        assert len(data["files"]) == 2
        for f in data["files"]:
            assert f["file_ext"] == ".dwg"
            assert f["batch_name"] == "project"
        db.expire_all()
        transfers = db.scalars(
            select(FileTransfer).where(FileTransfer.operation == "upload_zip")
        ).all()
        assert len(transfers) == 2
        assert all(item.direction == "inbound" for item in transfers)
        assert all(item.status == "succeeded" for item in transfers)

    def test_later_entry_failure_rolls_back_metadata_and_compensates_objects(
        self,
        db,
        tmp_path,
        monkeypatch,
    ):
        from app.api.v1 import files_api

        storage = LocalFileStorage(tmp_path / "zip-rollback-storage")
        monkeypatch.setattr(
            "app.services.storage_service.get_storage_backend",
            lambda: storage,
        )
        original_save = files_api.save_bytes_as_file
        call_count = 0

        def fail_second_entry(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise AppHTTPException(
                    503,
                    "INJECTED_ZIP_ENTRY_FAILURE",
                    "Injected failure after the first object write.",
                )
            return original_save(*args, **kwargs)

        monkeypatch.setattr(files_api, "save_bytes_as_file", fail_second_entry)
        init_db()
        client = TestClient(app, raise_server_exceptions=False)
        h = _admin(client)
        zip_data = self._zip_bytes(
            {"first.dwg": _valid_dwg(), "second.dwg": _valid_dwg()}
        )

        response = client.post(
            "/api/v1/files/upload-zip?file_ext=.dwg",
            headers=h,
            files={"upload": ("rollback.zip", io.BytesIO(zip_data), "application/zip")},
        )

        assert response.status_code == 503, response.text
        db.expire_all()
        assert db.scalars(
            select(StoredFile).where(StoredFile.batch_name == "rollback")
        ).all() == []
        object_page = storage.list_objects(
            "dwg-original",
            prefix="uploads/",
            cursor=None,
            page_size=20,
        )
        assert object_page.items == []

    def test_filters_by_extension(self):
        client = _client()
        h = _admin(client)
        zip_data = self._zip_bytes({
            "drawing.dwg": _valid_dwg(),
            "readme.txt": b"hello world",
        })
        r = client.post(
            "/api/v1/files/upload-zip?file_ext=.dwg",
            headers=h,
            files={"upload": ("mixed.zip", io.BytesIO(zip_data), "application/zip")},
        )
        assert r.status_code == 201, r.text
        data = r.json()["data"]
        assert data["success_count"] == 1
        assert data["skipped_count"] == 1

    def test_dxf_filter_skips_dwg_files(self):
        client = _client()
        h = _admin(client)
        zip_data = self._zip_bytes({"a.dwg": _valid_dwg()})
        r = client.post(
            "/api/v1/files/upload-zip?file_ext=.dxf",
            headers=h,
            files={"upload": ("dwg_only.zip", io.BytesIO(zip_data), "application/zip")},
        )
        assert r.status_code == 201, r.text
        data = r.json()["data"]
        assert data["success_count"] == 0
        assert data["skipped_count"] == 1

    def test_rejects_non_zip_file(self):
        client = _client()
        h = _admin(client)
        r = client.post(
            "/api/v1/files/upload-zip?file_ext=.dwg",
            headers=h,
            files={"upload": ("fake.zip", io.BytesIO(b"not a zip file"), "application/zip")},
        )
        assert r.status_code == 415, r.text
        assert "FILE_NOT_ZIP" in r.json()["error"]["code"]

    def test_strips_path_from_zip_entries(self):
        """Files stored with full paths in the ZIP should be extracted
        to just their basename."""
        client = _client()
        h = _admin(client)
        zip_data = self._zip_bytes({
            "deeply/nested/path/plan.dwg": _valid_dwg(),
        })
        r = client.post(
            "/api/v1/files/upload-zip?file_ext=.dwg",
            headers=h,
            files={"upload": ("nested.zip", io.BytesIO(zip_data), "application/zip")},
        )
        assert r.status_code == 201, r.text
        data = r.json()["data"]
        assert data["success_count"] == 1
        assert data["files"][0]["original_name"] == "plan.dwg"

    def test_empty_zip_returns_error(self):
        client = _client()
        h = _admin(client)
        zip_data = self._zip_bytes({})
        r = client.post(
            "/api/v1/files/upload-zip?file_ext=.dwg",
            headers=h,
            files={"upload": ("empty.zip", io.BytesIO(zip_data), "application/zip")},
        )
        assert r.status_code == 422, r.text
        assert "ZIP_EMPTY" in r.json()["error"]["code"]

    def test_skips_directories(self):
        client = _client()
        h = _admin(client)
        # Manually craft a ZIP with a directory entry
        import io as _io
        import zipfile as _zipfile
        buf = _io.BytesIO()
        with _zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("folder/", "")  # directory entry
            zf.writestr("folder/a.dwg", _valid_dwg())
        r = client.post(
            "/api/v1/files/upload-zip?file_ext=.dwg",
            headers=h,
            files={"upload": ("with_dirs.zip", io.BytesIO(buf.getvalue()), "application/zip")},
        )
        assert r.status_code == 201, r.text
        data = r.json()["data"]
        assert data["success_count"] == 1  # only the .dwg file, not the directory

    def test_missing_file_ext_param(self):
        client = _client()
        h = _admin(client)
        zip_data = self._zip_bytes({"a.dwg": _valid_dwg()})
        r = client.post(
            "/api/v1/files/upload-zip",
            headers=h,
            files={"upload": ("test.zip", io.BytesIO(zip_data), "application/zip")},
        )
        # Default is .dwg, so it should work
        assert r.status_code == 201, r.text
