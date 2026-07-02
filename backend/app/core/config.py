from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "DWG-Agent Platform"
    app_env: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    backend_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    database_url: str = "sqlite:///./var/app.db"

    storage_backend: Literal["local", "minio"] = "local"
    local_storage_root: Path = Path("./var/storage")
    max_upload_size_mb: int = 512

    jwt_secret_key: str = "change-me-in-dev"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30

    super_admin_username: str = "admin"
    super_admin_password: str = "admin123456"
    super_admin_real_name: str = "系统管理员"

    agent_enabled: bool = False
    dxf_pipeline_enabled: bool = False
    cad_worker_enabled: bool = False

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.backend_cors_origins.split(",") if item.strip()]



@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
