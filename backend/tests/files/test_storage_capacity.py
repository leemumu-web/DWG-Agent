from __future__ import annotations

from collections import namedtuple

import pytest

from app.platform.storage.local import LocalFileStorage
from app.platform.storage.minio import MinioStorage


def _metrics(*, total: str = "1000", free: str = "250") -> str:
    return "\n".join(
        [
            "# HELP minio_cluster_capacity_raw_total_bytes Total capacity",
            "# TYPE minio_cluster_capacity_raw_total_bytes gauge",
            f'minio_cluster_capacity_raw_total_bytes{{server="minio-1"}} {total}',
            "# HELP minio_cluster_capacity_raw_free_bytes Free capacity",
            f'minio_cluster_capacity_raw_free_bytes{{server="minio-1"}} {free}',
        ]
    )


def test_local_capacity_reports_real_usage_and_threshold(monkeypatch, tmp_path):
    usage = namedtuple("usage", "total used free")(1000, 800, 200)
    monkeypatch.setattr("app.platform.storage.local.shutil.disk_usage", lambda _path: usage)

    capacity = LocalFileStorage(tmp_path).capacity()

    assert capacity.status == "warning"
    assert capacity.total_bytes == 1000
    assert capacity.used_bytes == 800
    assert capacity.free_bytes == 200
    assert capacity.used_percent == 80.0
    assert capacity.reason is None
    assert capacity.checked_at.tzinfo is not None


@pytest.mark.parametrize(
    ("free", "expected_status", "expected_percent"),
    [
        ("201", "ok", 79.9),
        ("200", "warning", 80.0),
        ("100", "critical", 90.0),
    ],
)
def test_minio_capacity_uses_exact_warning_and_critical_boundaries(
    free,
    expected_status,
    expected_percent,
):
    storage = MinioStorage(
        endpoint="http://minio:9000",
        access_key="access",
        secret_key="secret",
        metrics_url="http://minio:9000/minio/v2/metrics/cluster",
        metrics_loader=lambda _url: _metrics(free=free),
    )

    capacity = storage.capacity()

    assert capacity.status == expected_status
    assert capacity.total_bytes == 1000
    assert capacity.used_percent == expected_percent


def test_minio_capacity_sums_all_cluster_server_series():
    payload = _metrics(total="1000", free="600") + "\n" + _metrics(
        total="2000",
        free="900",
    )
    storage = MinioStorage(
        endpoint="minio:9000",
        access_key="access",
        secret_key="secret",
        metrics_url="http://minio:9000/minio/v2/metrics/cluster",
        metrics_loader=lambda _url: payload,
    )

    capacity = storage.capacity()

    assert capacity.total_bytes == 3000
    assert capacity.free_bytes == 1500
    assert capacity.used_bytes == 1500
    assert capacity.used_percent == 50.0
    assert capacity.status == "ok"


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "minio_cluster_capacity_raw_total_bytes 1000",
        _metrics(total="not-a-number"),
        _metrics(total="0", free="0"),
        _metrics(total="100", free="101"),
        _metrics(total="100.5", free="20"),
    ],
)
def test_minio_capacity_returns_unknown_for_missing_or_invalid_metrics(payload):
    storage = MinioStorage(
        endpoint="minio:9000",
        access_key="access",
        secret_key="secret",
        metrics_url="http://minio:9000/minio/v2/metrics/cluster",
        metrics_loader=lambda _url: payload,
    )

    capacity = storage.capacity()

    assert capacity.status == "unknown"
    assert capacity.total_bytes is None
    assert capacity.used_bytes is None
    assert capacity.free_bytes is None
    assert capacity.used_percent is None
    assert capacity.reason == "capacity_metrics_invalid"


def test_minio_capacity_request_failure_is_unknown_not_zero():
    def _fail(_url: str) -> str:
        raise OSError("network unavailable")

    storage = MinioStorage(
        endpoint="minio:9000",
        access_key="access",
        secret_key="secret",
        metrics_url="http://minio:9000/minio/v2/metrics/cluster",
        metrics_loader=_fail,
    )

    capacity = storage.capacity()

    assert capacity.status == "unknown"
    assert capacity.total_bytes is None
    assert capacity.reason == "capacity_metrics_unavailable"


def test_minio_capacity_without_metrics_configuration_is_unknown():
    storage = MinioStorage(
        endpoint="minio:9000",
        access_key="access",
        secret_key="secret",
    )

    capacity = storage.capacity()

    assert capacity.status == "unknown"
    assert capacity.reason == "capacity_metrics_not_configured"
