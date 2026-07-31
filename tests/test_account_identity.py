from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from control_center.connector import (
    AccountIdentityConflictError,
    AccountIdentityMismatchError,
)
from control_center.models import Event, ZaloAccount

from .conftest import login


class _AccountInfoClient:
    def __init__(self, uid: str):
        self.uid = uid

    def fetchAccountInfo(self) -> dict:
        return {"profile": {"userId": self.uid, "displayName": "Identity fixture"}}


def _stored_account(
    client: TestClient,
    *,
    name: str,
    zalo_uid: str | None,
    listener_enabled: bool = True,
) -> str:
    secret_box = client.app.state.secret_box
    with client.app.state.database.session_factory() as session:
        account = ZaloAccount(
            name=name,
            zalo_uid=zalo_uid,
            cookie_cipher=secret_box.encrypt_json({"zpw_sek": f"cookie-{name}"}),
            imei_cipher=secret_box.encrypt_text(f"imei-{name}"),
            enabled=True,
            listener_enabled=listener_enabled,
            status="online" if zalo_uid else "unchecked",
        )
        session.add(account)
        session.commit()
        return account.id


def test_connector_test_account_keeps_identity_and_marks_matching_session_online(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    connector = client.app.state.zalo_connector
    account_id = _stored_account(client, name="same-identity", zalo_uid="zalo-100")
    monkeypatch.setattr(
        connector,
        "client_for",
        lambda _account: _AccountInfoClient("zalo-100"),
    )

    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        assert account is not None
        original_id = account.id

        payload = connector.test_account(session, account)

        session.refresh(account)
        assert payload["profile"]["userId"] == "zalo-100"
        assert account.id == original_id
        assert account.zalo_uid == "zalo-100"
        assert account.status == "online"
        assert account.listener_enabled is True
        assert account.last_error is None


def test_connector_test_account_failure_requires_admin_session_refresh(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    connector = client.app.state.zalo_connector
    account_id = _stored_account(client, name="expired-session", zalo_uid="zalo-101")

    def reject_session(_account):
        raise ValueError("candidate session cannot log in")

    monkeypatch.setattr(connector, "client_for", reject_session)

    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        assert account is not None

        with pytest.raises(ValueError, match="cannot log in"):
            connector.test_account(session, account)

        session.refresh(account)
        assert account.status == "error"
        assert account.listener_enabled is False
        assert account.last_error == "candidate session cannot log in"


def test_connector_test_account_rejects_a_different_identity_without_rebinding_history(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    connector = client.app.state.zalo_connector
    account_id = _stored_account(client, name="protected-history", zalo_uid="zalo-old")
    monkeypatch.setattr(
        connector,
        "client_for",
        lambda _account: _AccountInfoClient("zalo-other"),
    )

    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        assert account is not None

        with pytest.raises(AccountIdentityMismatchError):
            connector.test_account(session, account)

        session.refresh(account)
        assert account.zalo_uid == "zalo-old"
        assert account.status == "identity_mismatch"
        assert account.listener_enabled is False
        assert account.last_error
        assert "lịch sử" in account.last_error.casefold()


def test_connector_test_account_rejects_identity_owned_by_another_managed_account(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    connector = client.app.state.zalo_connector
    owner_id = _stored_account(client, name="uid-owner", zalo_uid="zalo-shared")
    candidate_id = _stored_account(client, name="empty-identity", zalo_uid=None)
    monkeypatch.setattr(
        connector,
        "client_for",
        lambda _account: _AccountInfoClient("zalo-shared"),
    )

    with client.app.state.database.session_factory() as session:
        candidate = session.get(ZaloAccount, candidate_id)
        assert candidate is not None

        with pytest.raises(AccountIdentityConflictError):
            connector.test_account(session, candidate)

        session.refresh(candidate)
        owner = session.get(ZaloAccount, owner_id)
        assert owner is not None
        assert owner.zalo_uid == "zalo-shared"
        assert candidate.zalo_uid is None
        assert candidate.status == "identity_mismatch"
        assert candidate.listener_enabled is False
        assert "uid-owner" in (candidate.last_error or "")


def _api_account_with_history(client: TestClient, csrf: str) -> tuple[str, int]:
    created = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "History owner",
            "imei": "imei-original-12345",
            "cookies": {"zpw_sek": "cookie-original"},
        },
    )
    assert created.status_code == 201
    account_id = created.json()["id"]

    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        assert account is not None
        account.zalo_uid = "zalo-history-owner"
        account.status = "online"
        event = Event(
            account_id=account_id,
            event_type="message.received",
            thread_id="thread-before-rotation",
            payload={"message": "must remain linked"},
        )
        session.add(event)
        session.commit()
        return account_id, event.id


def test_api_rejects_mismatched_session_before_replacing_encrypted_credentials(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    csrf = login(client)
    account_id, event_id = _api_account_with_history(client, csrf)
    secret_box = client.app.state.secret_box

    with client.app.state.database.session_factory() as session:
        before = session.get(ZaloAccount, account_id)
        assert before is not None
        old_cookie_cipher = before.cookie_cipher
        old_imei_cipher = before.imei_cipher

    monkeypatch.setattr(
        client.app.state.zalo_connector,
        "probe_session",
        lambda **_kwargs: ("zalo-someone-else", {"profile": {"userId": "zalo-someone-else"}}),
    )

    response = client.patch(
        f"/api/v1/accounts/{account_id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "cookies": {"zpw_sek": "cookie-wrong-owner"},
            "imei": "imei-wrong-owner-12345",
        },
    )

    assert response.status_code == 409
    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        event = session.get(Event, event_id)
        assert account is not None
        assert event is not None
        assert account.zalo_uid == "zalo-history-owner"
        assert account.cookie_cipher == old_cookie_cipher
        assert account.imei_cipher == old_imei_cipher
        assert secret_box.decrypt_json(account.cookie_cipher) == {
            "zpw_sek": "cookie-original"
        }
        assert event.account_id == account_id


def test_api_rejects_invalid_session_before_replacing_encrypted_credentials(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    csrf = login(client)
    account_id, event_id = _api_account_with_history(client, csrf)

    with client.app.state.database.session_factory() as session:
        before = session.get(ZaloAccount, account_id)
        assert before is not None
        old_cookie_cipher = before.cookie_cipher
        old_imei_cipher = before.imei_cipher

    def reject_probe(**_kwargs):
        raise ValueError("candidate session cannot log in")

    monkeypatch.setattr(client.app.state.zalo_connector, "probe_session", reject_probe)
    response = client.patch(
        f"/api/v1/accounts/{account_id}",
        headers={"X-CSRF-Token": csrf},
        json={"cookies": {"zpw_sek": "invalid-cookie"}},
    )

    assert response.status_code == 422
    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        event = session.get(Event, event_id)
        assert account is not None
        assert event is not None
        assert account.cookie_cipher == old_cookie_cipher
        assert account.imei_cipher == old_imei_cipher
        assert event.account_id == account_id


def test_api_rotates_matching_session_in_place_and_preserves_history_link(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    csrf = login(client)
    account_id, event_id = _api_account_with_history(client, csrf)
    seen: list[SimpleNamespace] = []

    def matching_probe(*, cookies, imei, user_agent):
        seen.append(SimpleNamespace(cookies=cookies, imei=imei, user_agent=user_agent))
        return "zalo-history-owner", {"profile": {"userId": "zalo-history-owner"}}

    monkeypatch.setattr(
        client.app.state.zalo_connector,
        "probe_session",
        matching_probe,
    )
    response = client.patch(
        f"/api/v1/accounts/{account_id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "cookies": {"zpw_sek": "cookie-rotated"},
            "imei": "imei-rotated-12345",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == account_id
    assert response.json()["identity_verified"] is True
    assert response.json()["history_preserved"] is True
    assert len(seen) == 1
    assert seen[0].cookies == {"zpw_sek": "cookie-rotated"}
    assert seen[0].imei == "imei-rotated-12345"

    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        event = session.scalar(select(Event).where(Event.id == event_id))
        assert account is not None
        assert event is not None
        assert account.id == account_id
        assert account.zalo_uid == "zalo-history-owner"
        assert client.app.state.secret_box.decrypt_json(account.cookie_cipher) == {
            "zpw_sek": "cookie-rotated"
        }
        assert client.app.state.secret_box.decrypt_text(account.imei_cipher) == (
            "imei-rotated-12345"
        )
        assert account.listener_enabled is False
        assert account.status == "online"
        assert event.account_id == account_id


def test_new_account_cannot_start_listener_before_uid_verification(client: TestClient):
    csrf = login(client)
    created = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Unverified listener",
            "imei": "imei-unverified-12345",
            "cookies": {"zpw_sek": "cookie-unverified"},
            "listener_enabled": True,
        },
    )
    assert created.status_code == 201
    assert created.json()["listener_enabled"] is False
    assert created.json()["identity_locked"] is False

    enabled = client.post(
        f"/api/v1/accounts/{created.json()['id']}/listener/true",
        headers={"X-CSRF-Token": csrf},
    )
    assert enabled.status_code == 409
    assert "Zalo UID" in enabled.json()["detail"]
