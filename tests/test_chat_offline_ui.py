from __future__ import annotations

from fastapi.testclient import TestClient

from control_center.models import Event, ZaloAccount

from .conftest import login


def test_disabled_account_history_remains_available_in_chat(client: TestClient):
    csrf = login(client)
    with client.app.state.database.session_factory() as session:
        account = ZaloAccount(
            name="Tài khoản mất kết nối",
            cookie_cipher="encrypted-cookie",
            imei_cipher="encrypted-imei",
            enabled=False,
            listener_enabled=False,
            status="offline",
        )
        session.add(account)
        session.flush()
        account_id = account.id
        session.add(
            Event(
                account_id=account_id,
                event_type="message.received",
                thread_id="offline-thread-1",
                payload={
                    "message_id": "offline-message-1",
                    "author_id": "offline-friend-1",
                    "message": "Lịch sử cũ vẫn còn",
                    "message_object": {"dName": "Người liên hệ cũ"},
                    "thread_type": "USER",
                },
            )
        )
        session.commit()

    page = client.get("/chat")
    assert page.status_code == 200
    assert "20260901-shared-stickers-1" in page.text
    assert "Tài khoản mất kết nối · chỉ đọc" in page.text
    assert 'data-enabled="false"' in page.text
    assert (
        "Mất kết nối — lịch sử vẫn an toàn; cập nhật phiên để gửi tiếp"
        in page.text
    )

    conversations = client.get(
        "/api/v1/chat/conversations",
        params={"account_id": account_id},
    )
    assert conversations.status_code == 200
    assert [item["thread_id"] for item in conversations.json()] == [
        "offline-thread-1"
    ]

    messages = client.get(
        "/api/v1/chat/messages",
        params={"account_id": account_id, "thread_id": "offline-thread-1"},
    )
    assert messages.status_code == 200
    assert messages.json()[0]["text"] == "Lịch sử cũ vẫn còn"

    blocked = client.post(
        "/api/v1/chat/messages",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "thread_id": "offline-thread-1",
            "thread_type": "user",
            "text": "Không được xếp hàng khi tài khoản đang tắt",
        },
    )
    assert blocked.status_code == 409
    assert "disabled" in blocked.json()["detail"].lower()
