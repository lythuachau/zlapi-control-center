from __future__ import annotations

from pathlib import Path

from control_center.config import Settings
from control_center.maintenance import _pg_environment


def test_pg_environment_keeps_password_out_of_process_arguments(tmp_path: Path) -> None:
    settings = Settings(
        app_env="test",
        database_url=(
            "postgresql+psycopg://backup_user:p%40ssword@db.example.com:25432/"
            "control?sslmode=require"
        ),
        data_dir=tmp_path,
        session_secret="test-session-secret-that-is-long-enough",
    )

    environment = _pg_environment(settings)

    assert environment["PGHOST"] == "db.example.com"
    assert environment["PGPORT"] == "25432"
    assert environment["PGUSER"] == "backup_user"
    assert environment["PGPASSWORD"] == "p@ssword"
    assert environment["PGDATABASE"] == "control"
    assert environment["PGSSLMODE"] == "require"
