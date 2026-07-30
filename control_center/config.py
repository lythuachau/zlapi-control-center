from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.getenv("ZLAPI_ENV_FILE", ".env"),
        env_prefix="ZLAPI_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "ZLAPI Control Center"
    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./zlapi-control.db"
    session_secret: str = "development-only-change-me-please"
    data_dir: Path = Path(".data")
    domain: str = "localhost"
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    cookie_secure: bool = False
    auto_create_schema: bool = True
    database_cap_bytes: int = 1_073_741_824
    max_upload_mb: int = Field(default=25, ge=1, le=250)
    archive_after_days: int = Field(default=90, ge=7)
    pg_dump_path: str = "pg_dump"
    rclone_remote: str = ""
    alert_webhook_url: str = ""
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = ""
    request_timeout_seconds: int = Field(default=30, ge=5, le=120)
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    media_process_timeout_seconds: int = Field(default=90, ge=10, le=300)
    api_key_requests_per_minute: int = Field(default=120, ge=10, le=10_000)
    session_requests_per_minute: int = Field(default=300, ge=30, le=20_000)
    login_attempts_per_minute: int = Field(default=10, ge=3, le=100)
    worker_id: str = ""

    @property
    def normalized_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url.removeprefix("postgres://")
        if url.startswith("postgresql://") and "+psycopg" not in url:
            return "postgresql+psycopg://" + url.removeprefix("postgresql://")
        return url

    @property
    def is_postgres(self) -> bool:
        return self.normalized_database_url.startswith("postgresql+psycopg://")

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def secret_dir(self) -> Path:
        return self.data_dir / "secrets"

    @model_validator(mode="after")
    def validate_production(self) -> Settings:
        if self.app_env == "production":
            if not self.is_postgres:
                raise ValueError("Production requires an external PostgreSQL database")
            if len(self.session_secret) < 32 or self.session_secret.startswith("development-"):
                raise ValueError("Production requires a random session secret of at least 32 characters")
            if not self.cookie_secure:
                raise ValueError("Production requires secure cookies")
            if "sslmode=require" not in self.normalized_database_url.lower():
                raise ValueError("Production PostgreSQL must use sslmode=require")
        return self

    def ensure_directories(self) -> None:
        for directory in (
            self.data_dir,
            self.upload_dir,
            self.archive_dir,
            self.backup_dir,
            self.secret_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
