from __future__ import annotations

from fastapi.testclient import TestClient

from control_center.models import Event, ZaloAccount

from .conftest import login


def test_chat_messages_can_load_older_pages_without_overlap(client: TestClient):
    login(client)
    database = client.app.state.database
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Pagination account",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        session.add(account)
        session.flush()
        session.add_all(
            [
                Event(
                    account_id=account.id,
                    event_type="message.received",
                    thread_id="pagination-thread",
                    payload={
                        "message_id": f"pagination-message-{index}",
                        "author_id": "friend",
                        "message": f"Tin lịch sử {index}",
                        "message_object": {"dName": "Người gửi"},
                        "thread_type": "USER",
                    },
                )
                for index in range(1, 6)
            ]
        )
        session.commit()
        account_id = account.id

    latest = client.get(
        "/api/v1/chat/messages",
        params={
            "account_id": account_id,
            "thread_id": "pagination-thread",
            "limit": 2,
        },
    )
    assert latest.status_code == 200
    latest_items = latest.json()
    assert [item["text"] for item in latest_items] == ["Tin lịch sử 4", "Tin lịch sử 5"]

    older = client.get(
        "/api/v1/chat/messages",
        params={
            "account_id": account_id,
            "thread_id": "pagination-thread",
            "before_event_id": latest_items[0]["event_id"],
            "limit": 2,
        },
    )
    assert older.status_code == 200
    older_items = older.json()
    assert [item["text"] for item in older_items] == ["Tin lịch sử 2", "Tin lịch sử 3"]
    assert {item["id"] for item in older_items}.isdisjoint(
        {item["id"] for item in latest_items}
    )

    oldest = client.get(
        "/api/v1/chat/messages",
        params={
            "account_id": account_id,
            "thread_id": "pagination-thread",
            "before_event_id": older_items[0]["event_id"],
            "limit": 2,
        },
    )
    assert oldest.status_code == 200
    assert [item["text"] for item in oldest.json()] == ["Tin lịch sử 1"]


def test_chat_messages_rejects_ambiguous_cursors(client: TestClient):
    csrf = login(client)
    with client.app.state.database.session_factory() as session:
        account = ZaloAccount(
            name="Cursor account",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        session.add(account)
        session.commit()
        account_id = account.id

    response = client.get(
        "/api/v1/chat/messages",
        params={
            "account_id": account_id,
            "thread_id": "pagination-thread",
            "around_event_id": 10,
            "before_event_id": 5,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
