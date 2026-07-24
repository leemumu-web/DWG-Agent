from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.paths import REPO_ROOT

NGINX_CONFIGS = (
    REPO_ROOT / "infra/gateway/nginx/nginx.conf",
    REPO_ROOT / "infra/gateway/nginx/nginx.local.conf",
)


@pytest.mark.parametrize("config_path", NGINX_CONFIGS)
def test_nginx_preserves_fastapi_error_status_and_json(config_path: Path):
    content = config_path.read_text(encoding="utf-8")

    assert "proxy_intercept_errors off;" in content
    assert "error_page 503" not in content
    assert 'return 502 \'{"error":{"code":"BAD_GATEWAY"' in content
    assert 'return 504 \'{"error":{"code":"GATEWAY_TIMEOUT"' in content


@pytest.mark.parametrize("config_path", NGINX_CONFIGS)
def test_nginx_sse_route_has_streaming_contract(config_path: Path):
    content = config_path.read_text(encoding="utf-8")
    route_start = content.index("location ~ ^/api/v1/workflows/jobs/[0-9]+/events$")
    route_end = content.index("}", route_start)
    route = content[route_start:route_end]

    assert "proxy_read_timeout 1h;" in route
    assert "proxy_send_timeout 1h;" in route
    assert "proxy_buffering off;" in route
    assert "proxy_cache off;" in route
    assert "add_header X-Accel-Buffering no always;" in route


@pytest.mark.parametrize("config_path", NGINX_CONFIGS)
def test_nginx_forwards_request_identity_and_client_context(config_path: Path):
    content = config_path.read_text(encoding="utf-8")

    for header in (
        "Host $host",
        "X-Real-IP $remote_addr",
        "X-Forwarded-For $proxy_add_x_forwarded_for",
        "X-Forwarded-Proto $scheme",
        "X-Request-ID $request_id",
    ):
        assert f"proxy_set_header {header};" in content


def test_local_nginx_buffers_uploads_inside_the_owned_runtime_directory():
    content = NGINX_CONFIGS[1].read_text(encoding="utf-8")

    expected = REPO_ROOT / "infra/gateway/nginx/logs/client-body"
    assert f"client_body_temp_path {expected} 1 2;" in content
    assert "client_body_temp_path /var/lib/nginx/client-body" not in content
