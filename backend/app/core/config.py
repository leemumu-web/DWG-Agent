from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote as url_quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "DWG-Agent Platform"
    app_env: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    backend_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Optional full-DSN override. Runtime defaults to the MYSQL_* component fields;
    # tests use DATABASE_URL=sqlite:// for isolated in-memory sessions.
    database_url: str | None = None

    # MySQL component fields (spec §18); Docker overrides host to the service name mysql
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_database: str = "dwg_agent"
    mysql_user: str = "dwg_user"
    mysql_password: str = ""

    storage_backend: Literal["local", "minio"] = "local"
    local_storage_root: Path = Path("./var/storage")
    max_upload_size_mb: int = 512
    max_zip_extract_mb: int = 2048     # max total uncompressed size when extracting a ZIP
    max_zip_entry_count: int = 1000    # max number of files inside a single ZIP

    jwt_secret_key: str = "change-me-in-dev-change-me-in-prod-32chars"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 14
    # Refresh-cookie Secure flag. None = auto (Secure iff app_env=="production").
    # Set False explicitly for HTTP-only intranet deployments (e.g. company VPN
    # with no public exposure) where a Secure cookie would be silently dropped
    # by the browser, breaking the refresh flow. Never set False on public TLS
    # frontends — the 14-day refresh token must not travel in cleartext there.
    refresh_cookie_secure: bool | None = None

    super_admin_username: str = "admin"
    super_admin_password: str = "SuperAdminPass1"
    super_admin_real_name: str = "系统管理员"

    agent_enabled: bool = False
    dxf_pipeline_enabled: bool = False
    dxf2dwg_pipeline_enabled: bool = False
    cad_worker_enabled: bool = False

    # DWG→DXF — ODA Converter 引擎参数
    oda_converter_version: str = "ACAD2018"
    oda_converter_audit: bool = True
    oda_converter_timeout: int = 300
    oda_converter_retries: int = 1
    oda_xvfb_run: bool = True
    # DXF→DWG — 同 ODA，输入输出互换
    dxf2dwg_converter_version: str = "ACAD2018"
    dxf2dwg_converter_audit: bool = True
    dxf2dwg_converter_timeout: int = 300
    dxf2dwg_converter_retries: int = 1
    oda_home: str = ""

    # DXF→Excel material-table extraction
    dxf2excel_pipeline_enabled: bool = False

    # Excel→final part-list processing (excel_final pipeline)
    excel_final_pipeline_enabled: bool = False

    # LLM — spec §18.1 (Stage 2: Agent subsystem)
    model_name: str = "deepseek-chat"
    model_api_key: str = ""
    model_base_url: str = "https://api.deepseek.com"

    # MCP — spec §18.1 (Stage 2: MCP client)
    mcp_cad_command: str = "uvx"
    mcp_cad_args: str = "cad-mcp-server,stdio"

    # CAD Worker — spec §18.1 (Stage 4: Windows CAD node)
    cad_worker_api_base: str = "http://cad-worker.internal:8080"
    cad_worker_api_key: str = ""

    # Agent memory retention (seconds) — applies to MySQL-backed agent_memory rows
    agent_memory_ttl: int = 7200
    agent_max_messages: int = 20
    celery_task_always_eager: bool = False

    @property
    def sqlalchemy_database_url(self) -> str:
        """Return the authoritative SQLAlchemy DSN for the application database."""
        return self.database_url or self.mysql_url

    @property
    def celery_database_url(self) -> str:
        """Return the MySQL DSN shared by Celery's broker and result backend.

        A MySQL ``DATABASE_URL`` override is authoritative. SQLite is reserved for
        eager unit tests, where Celery still exposes the production-shaped MySQL
        transport configuration but never opens a broker connection.
        """
        if self.database_url and self.database_url.startswith("mysql"):
            return self.database_url
        return self.mysql_url

    @property
    def celery_broker_url(self) -> str:
        """Return Celery's SQLAlchemy broker URL using the authoritative MySQL DSN."""
        return f"sqla+{self.celery_database_url}"

    @property
    def celery_result_backend(self) -> str:
        """Return Celery's database result-backend URL using the same MySQL DSN."""
        return f"db+{self.celery_database_url}"

    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    # Bucket names per spec §10.2
    minio_bucket_original: str = "dwg-original"
    minio_bucket_derived: str = "dwg-derived"
    minio_bucket_reports: str = "dwg-reports"
    minio_bucket_temp: str = "dwg-temp"
    # Direction-specific buckets for DXF uploads and DXF derived results
    minio_bucket_dxf_original: str = "dxf-original"
    minio_bucket_dxf_derived: str = "dxf-derived"

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.backend_cors_origins.split(",") if item.strip()]

    @property
    def mysql_url(self) -> str:
        """Assemble MySQL URL from component fields (spec §18), URL-encoding the password."""
        user_part = (
            f"{self.mysql_user}:{url_quote(self.mysql_password, safe='')}"
            if self.mysql_password
            else self.mysql_user
        )
        return (
            f"mysql+pymysql://{user_part}@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )

    @property
    def refresh_cookie_secure_enabled(self) -> bool:
        """Resolve the refresh-cookie Secure flag.

        Defaults to ``app_env == "production"``; override explicitly via the
        ``REFRESH_COOKIE_SECURE`` env var (e.g. ``false`` for an HTTP-only
        intranet deployment behind a VPN with no public exposure).
        """
        if self.refresh_cookie_secure is not None:
            return self.refresh_cookie_secure
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
