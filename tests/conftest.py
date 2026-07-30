from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_center.config import Settings
from control_center.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        data_dir=tmp_path / "data",
        session_secret="test-session-secret-that-is-long-enough",
        bootstrap_admin_username="admin",
        bootstrap_admin_password="VeryStrongPassword123!",
        auto_create_schema=True,
        trusted_hosts="testserver,localhost",
        login_attempts_per_minute=3,
    )


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str = "admin", password: str = "VeryStrongPassword123!") -> str:
    page = client.get("/login")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match
    csrf = match.group(1)
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', dashboard.text)
    assert match
    return match.group(1)
