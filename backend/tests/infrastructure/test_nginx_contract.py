from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.paths import REPO_ROOT


def test_native_mysql_console_has_no_external_database_upstream():
    text = (REPO_ROOT / "infra/gateway/nginx/nginx.conf").read_text(encoding="utf-8")
    assert "upstream cloudbeaver" not in text
    assert "location = /_dba_auth" not in text
    assert "location /dba/mysql/" not in text

NGINX_CONFIGS = (
    REPO_ROOT / "infra/gateway/nginx/nginx.conf",
    REPO_ROOT / "infra/gateway/nginx/nginx.local.conf",
)
START_ALL = REPO_ROOT / "scripts/start-all.sh"
STOP_ALL = REPO_ROOT / "scripts/stop-all.sh"
INFRA_VERIFY = REPO_ROOT / "infra/verification/verify.sh"


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

    assert "client_body_temp_path infra/gateway/nginx/logs/client-body 1 2;" in content
    assert "client_body_temp_path /var/lib/nginx/client-body" not in content
    assert str(REPO_ROOT) not in content
    assert "/home/Creeken/" not in content


def test_local_nginx_commands_bind_relative_paths_to_the_repository_prefix():
    start_source = START_ALL.read_text(encoding="utf-8")
    stop_source = STOP_ALL.read_text(encoding="utf-8")
    verify_source = INFRA_VERIFY.read_text(encoding="utf-8")

    assert 'nginx -p "$PROJECT_ROOT/" -c "$NGINX_CONF"' in start_source
    assert 'nginx -p "$PROJECT_ROOT/" -c "$NGINX_CONF"' in stop_source
    assert 'nginx -p "$PROJECT_ROOT/" -t -c "$PROJECT_ROOT/$NGINX_LOCAL"' in verify_source


@pytest.mark.parametrize("config_path", NGINX_CONFIGS)
def test_nginx_streams_large_api_uploads_with_multipart_headroom(config_path: Path):
    content = config_path.read_text(encoding="utf-8")
    route_start = content.index("location /api/")
    route_end = content.index("}", route_start)
    route = content[route_start:route_end]

    assert "client_max_body_size 520m;" in content
    assert "proxy_request_buffering off;" in route
    assert "rl=$request_length rid=$request_id" in content


@pytest.mark.parametrize("config_path", NGINX_CONFIGS)
def test_nginx_host_allowlist_accepts_private_lan_addresses(config_path: Path):
    content = config_path.read_text(encoding="utf-8")

    assert "map $host $host_is_allowed" in content
    assert r"~^100\.(6[4-9]|[78][0-9]|9[0-9]|1[01][0-9]|12[0-7])\." in content
    assert r"~^10\." in content
    assert r"~^172\.(1[6-9]|2[0-9]|3[01])\." in content
    assert r"~^192\.168\." in content
    assert "if ($host_is_allowed = 0)" in content
