from __future__ import annotations

import re
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select

from control_center.auth import require_short_lived_scope
from control_center.models import AuditLog, Event, Job, User, ZaloAccount, utcnow

from .conftest import login


def test_login_dashboard_and_catalog(client: TestClient):
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/api/openapi.json").status_code == 401
    login(client)
    assert "ZLAPI Control Center" in client.get("/").text
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/openapi.json").status_code == 200
    catalog = client.get("/api/v1/catalog")
    assert catalog.status_code == 200
    names = {item["name"] for item in catalog.json()}
    assert {"sendMessage", "createPoll", "changeGroupDesc", "loginWithQr"} <= names
    unsupported = {item["name"] for item in catalog.json() if item["status"] == "unsupported"}
    assert {"changeGroupDesc", "loginWithQr", "votePoll", "assignTask"} <= unsupported


def test_account_secrets_are_never_returned(client: TestClient):
    csrf = login(client)
    response = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Tài khoản thử",
            "imei": "imei-value-12345",
            "cookies": {"zpsid": "secret-cookie", "zpw_sek": "secret-two"},
            "listener_enabled": False,
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["has_credentials"] is True
    assert "cookie" not in payload
    assert "imei" not in payload
    listing = client.get("/api/v1/accounts").text
    assert "secret-cookie" not in listing
    assert "imei-value" not in listing
    listener = client.post(
        f"/api/v1/accounts/{payload['id']}/listener/true",
        headers={"X-CSRF-Token": csrf},
    )
    assert listener.status_code == 200
    assert listener.json()["listener_enabled"] is True
    rotated = client.patch(
        f"/api/v1/accounts/{payload['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"cookies": {"zpw_sek": "rotated-secret"}},
    )
    assert rotated.status_code == 200
    assert rotated.json()["listener_enabled"] is False
    assert rotated.json()["status"] == "unchecked"
    assert "rotated-secret" not in rotated.text


def test_admin_can_delete_account_but_history_is_retained(client: TestClient):
    csrf = login(client)
    created = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Disposable account",
            "imei": "imei-value-12345",
            "cookies": {"zpw_sek": "secret-cookie"},
        },
    ).json()
    account_id = created["id"]
    queued = client.post(
        f"/api/v1/accounts/{account_id}/test",
        headers={"X-CSRF-Token": csrf},
    )
    assert queued.status_code == 202
    with client.app.state.database.session_factory() as session:
        session.add(
            Event(
                account_id=account_id,
                event_type="message.received",
                payload={"message": "retained"},
            )
        )
        session.commit()

    rejected = client.request(
        "DELETE",
        f"/api/v1/accounts/{account_id}",
        headers={"X-CSRF-Token": csrf},
        json={"confirmation": "wrong name"},
    )
    assert rejected.status_code == 422

    deleted = client.request(
        "DELETE",
        f"/api/v1/accounts/{account_id}",
        headers={"X-CSRF-Token": csrf},
        json={"confirmation": "Disposable account"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["retained_jobs"] == 1
    assert deleted.json()["retained_events"] == 1

    with client.app.state.database.session_factory() as session:
        assert session.get(ZaloAccount, account_id) is None
        job = session.get(Job, queued.json()["job_id"])
        assert job is not None
        assert job.account_id is None
        assert job.status == "cancelled"
        event = session.scalar(
            select(Event).where(Event.event_type == "message.received")
        )
        assert event is not None
        assert event.account_id is None
        audit = session.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "account.delete",
                AuditLog.resource_id == account_id,
            )
            .order_by(AuditLog.id.desc())
        )
        assert audit is not None


def test_jobs_can_be_filtered_by_zalo_account(client: TestClient):
    csrf = login(client)
    account_one = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Jobs account one",
            "imei": "imei-jobs-one-12345",
            "cookies": {"zpw_sek": "jobs-one-secret"},
        },
    ).json()
    account_two = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Jobs account two",
            "imei": "imei-jobs-two-12345",
            "cookies": {"zpw_sek": "jobs-two-secret"},
        },
    ).json()
    first_job = client.post(
        f"/api/v1/accounts/{account_one['id']}/test",
        headers={"X-CSRF-Token": csrf},
    )
    second_job = client.post(
        f"/api/v1/accounts/{account_two['id']}/test",
        headers={"X-CSRF-Token": csrf},
    )
    assert first_job.status_code == 202
    assert second_job.status_code == 202

    filtered = client.get(
        "/api/v1/jobs",
        params={"account_id": account_one["id"]},
    )
    assert filtered.status_code == 200
    assert [job["id"] for job in filtered.json()] == [first_job.json()["job_id"]]
    assert filtered.json()[0]["account_id"] == account_one["id"]

    missing = client.get(
        "/api/v1/jobs",
        params={"account_id": "missing-account"},
    )
    assert missing.status_code == 404


def test_dangerous_action_requires_exact_confirmation(client: TestClient):
    csrf = login(client)
    account = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Danger test", "imei": "imei-12345", "cookies": {"a": "b"}},
    ).json()
    body = {
        "account_id": account["id"],
        "payload": {"userId": "123"},
    }
    rejected = client.post(
        "/api/v1/actions/blockUser",
        headers={"X-CSRF-Token": csrf},
        json=body,
    )
    assert rejected.status_code == 422
    body["confirmation"] = "blockUser"
    accepted = client.post(
        "/api/v1/actions/blockUser",
        headers={"X-CSRF-Token": csrf},
        json=body,
    )
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "queued"


def test_csrf_blocks_session_mutations(client: TestClient):
    login(client)
    response = client.post(
        "/api/v1/accounts",
        json={"name": "No CSRF", "imei": "imei-12345", "cookies": {"a": "b"}},
    )
    assert response.status_code == 403


def test_stream_auth_releases_database_connection_before_streaming(client: TestClient):
    login(client)
    with client.app.state.database.session_factory() as session:
        admin = session.scalar(select(User).where(User.username == "admin"))
        admin_id = admin.id

    counts = {"checkout": 0, "checkin": 0}

    def on_checkout(*_):
        counts["checkout"] += 1

    def on_checkin(*_):
        counts["checkin"] += 1

    engine = client.app.state.database.engine
    sqlalchemy_event.listen(engine, "checkout", on_checkout)
    sqlalchemy_event.listen(engine, "checkin", on_checkin)
    try:
        request = SimpleNamespace(
            app=client.app,
            headers={},
            session={"user_id": admin_id},
        )
        principal = require_short_lived_scope("read")(request)
    finally:
        sqlalchemy_event.remove(engine, "checkout", on_checkout)
        sqlalchemy_event.remove(engine, "checkin", on_checkin)

    assert principal.id == admin_id
    assert counts == {"checkout": 1, "checkin": 1}


def test_user_can_change_own_password_and_must_sign_in_again(client: TestClient):
    csrf = login(client)
    profile = client.get("/profile")
    assert profile.status_code == 200
    assert 'id="password-change-form"' in profile.text
    assert "@admin" in profile.text

    missing_csrf = client.put(
        "/api/v1/me/password",
        json={
            "current_password": "VeryStrongPassword123!",
            "new_password": "NewSecurePassword456!",
        },
    )
    assert missing_csrf.status_code == 403

    wrong_current = client.put(
        "/api/v1/me/password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": "IncorrectPassword123!",
            "new_password": "NewSecurePassword456!",
        },
    )
    assert wrong_current.status_code == 422
    assert wrong_current.json()["detail"] == "Mật khẩu hiện tại không đúng"
    assert client.get("/profile").status_code == 200

    weak_password = client.put(
        "/api/v1/me/password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": "VeryStrongPassword123!",
            "new_password": "alllowercasepassword",
        },
    )
    assert weak_password.status_code == 422

    changed = client.put(
        "/api/v1/me/password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": "VeryStrongPassword123!",
            "new_password": "NewSecurePassword456!",
        },
    )
    assert changed.status_code == 200
    assert changed.json() == {"changed": True, "reauthenticate": True}
    assert client.get("/profile", follow_redirects=False).status_code == 303

    old_login_page = client.get("/login")
    old_csrf = re.search(r'name="csrf_token" value="([^"]+)"', old_login_page.text).group(1)
    old_login = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "VeryStrongPassword123!",
            "csrf_token": old_csrf,
        },
        follow_redirects=False,
    )
    assert old_login.status_code == 303
    assert "error=invalid-credentials" in old_login.headers["location"]

    login(client, password="NewSecurePassword456!")
    with client.app.state.database.session_factory() as session:
        admin = session.scalar(select(User).where(User.username == "admin"))
        audit = session.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "auth.password_change",
                AuditLog.resource_id == admin.id,
            )
            .order_by(AuditLog.id.desc())
        )
        assert audit is not None
        assert audit.details == {"username": "admin"}


def test_admin_can_create_user_and_api_key(client: TestClient):
    csrf = login(client)
    user = client.post(
        "/api/v1/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "username": "operator.one",
            "display_name": "Operator One",
            "password": "OperatorPassword123!",
            "role": "operator",
        },
    )
    assert user.status_code == 201
    key = client.post(
        "/api/v1/api-keys",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Automation", "scopes": ["read", "jobs:create"]},
    )
    assert key.status_code == 201
    secret = key.json()["api_key"]
    assert secret.startswith("zlc_")
    authenticated = client.get(
        "/api/v1/catalog", headers={"Authorization": f"Bearer {secret}"}
    )
    assert authenticated.status_code == 200
    assert secret not in client.get("/api/v1/api-keys").text


def test_login_endpoint_is_rate_limited(client: TestClient):
    page = client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    for _ in range(3):
        response = client.post(
            "/login",
            data={"username": "missing", "password": "wrong", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert response.status_code == 303
    limited = client.post(
        "/login",
        data={"username": "missing", "password": "wrong", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert limited.status_code == 429


def test_chat_projects_listener_events_and_queues_safe_send(client: TestClient):
    csrf = login(client)
    created = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Chat account",
            "imei": "imei-chat-12345",
            "cookies": {"zpw_sek": "chat-secret"},
            "listener_enabled": True,
        },
    ).json()
    account_id = created["id"]
    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        account.status = "online"
        account.zalo_uid = "self-uid"
        account.listener_enabled = True
        session.add(
            Event(
                account_id=account_id,
                event_type="message.received",
                thread_id="thread-1001",
                payload={
                    "message_id": "message-1",
                    "author_id": "friend-2002",
                    "message": "Xin chào từ Listener",
                    "message_object": {"dName": "Minh Anh"},
                    "thread_type": "USER",
                },
            )
        )
        session.add(
            Event(
                account_id=account_id,
                event_type="message.received",
                thread_id="group-thread-379842",
                payload={
                    "message_id": "group-message-1",
                    "author_id": "member-3003",
                    "message": "Cập nhật tiến độ hôm nay",
                    "message_object": {"dName": "Nguyễn Văn Hưởng"},
                    "thread_type": "GROUP",
                },
            )
        )
        session.add(
            Event(
                account_id=account_id,
                event_type="message.received",
                thread_id="media-thread-4004",
                payload={
                    "message_id": "photo-message-1",
                    "author_id": "friend-4004",
                    "message": {
                        "href": "https://photo-stal-2.zdn.vn/photo.jpg",
                        "thumb": "https://photo-stal-2.zdn.vn/photo-thumb.jpg",
                        "params": '{"width": 1280, "height": 720}',
                    },
                    "message_object": {
                        "dName": "Khánh Linh",
                        "msgType": "chat.photo",
                    },
                    "thread_type": "USER",
                },
            )
        )
        session.add(
            Event(
                account_id=account_id,
                event_type="message.received",
                thread_id="media-thread-4004",
                payload={
                    "message_id": "file-message-1",
                    "author_id": "friend-4004",
                    "message": {
                        "title": "bao-gia-thang-8.pdf",
                        "href": "https://f41-zfcloud.zdn.vn/bao-gia-thang-8.pdf",
                        "params": '{"fileSize": 245760, "fileExt": "pdf"}',
                    },
                    "message_object": {
                        "dName": "Khánh Linh",
                        "msgType": "share.file",
                    },
                    "thread_type": "USER",
                },
            )
        )
        session.add(
            Event(
                account_id=account_id,
                event_type="message.received",
                thread_id="media-thread-4004",
                payload={
                    "message_id": "reaction-message-1",
                    "author_id": "friend-4004",
                    "message": {"rIcon": "/-strong"},
                    "message_object": {
                        "dName": "Khánh Linh",
                        "msgType": "chat.reaction",
                    },
                    "thread_type": "USER",
                },
            )
        )
        session.add(
            Event(
                account_id=account_id,
                event_type="group.metadata.synced",
                payload={
                    "groups": {
                        "group-thread-379842": {
                            "name": "ĐỒNG HÀNH CÙNG AI",
                            "avatar_url": "https://s120-ava-grp-talk.zadn.vn/group.jpg",
                            "member_count": 99,
                        }
                    },
                    "group_count": 1,
                },
            )
        )
        session.commit()

    page = client.get("/chat")
    assert page.status_code == 200
    assert "ZCHAT DESK" in page.text
    assert "Chat account" in page.text

    conversations = client.get(
        "/api/v1/chat/conversations",
        params={"account_id": account_id},
    )
    assert conversations.status_code == 200
    personal = next(
        item for item in conversations.json() if item["thread_id"] == "thread-1001"
    )
    assert personal["name"] == "Minh Anh"
    assert personal["thread_type"] == "user"
    assert personal["preview"] == "Xin chào từ Listener"
    assert personal["unread_count"] == 1
    assert personal["first_unread_event_id"] == personal["latest_event_id"]

    group = next(
        item
        for item in conversations.json()
        if item["thread_id"] == "group-thread-379842"
    )
    assert group["name"] == "ĐỒNG HÀNH CÙNG AI"
    assert group["thread_type"] == "group"
    assert group["preview_author_name"] == "Nguyễn Văn Hưởng"
    assert group["member_count"] == 99
    assert group["avatar_url"].startswith("https://")
    assert group["unread_count"] == 1

    media_conversation = next(
        item
        for item in conversations.json()
        if item["thread_id"] == "media-thread-4004"
    )
    assert media_conversation["unread_count"] == 2
    assert media_conversation["first_unread_event_id"] is not None
    assert (
        media_conversation["latest_event_id"]
        > media_conversation["first_unread_event_id"]
    )

    messages = client.get(
        "/api/v1/chat/messages",
        params={"account_id": account_id, "thread_id": "thread-1001"},
    )
    assert messages.status_code == 200
    assert messages.json()[0]["mine"] is False
    assert messages.json()[0]["text"] == "Xin chào từ Listener"

    group_messages = client.get(
        "/api/v1/chat/messages",
        params={"account_id": account_id, "thread_id": "group-thread-379842"},
    )
    assert group_messages.status_code == 200
    assert group_messages.json()[0]["author_name"] == "Nguyễn Văn Hưởng"
    assert group_messages.json()[0]["thread_name"] == "Nhóm Zalo • 379842"

    media_messages = client.get(
        "/api/v1/chat/messages",
        params={"account_id": account_id, "thread_id": "media-thread-4004"},
    )
    assert media_messages.status_code == 200
    media_by_id = {item["id"]: item for item in media_messages.json()}
    assert set(media_by_id) == {"photo-message-1", "file-message-1"}
    assert media_by_id["photo-message-1"]["kind"] == "image"
    assert media_by_id["photo-message-1"]["text"] == "[Hình ảnh]"
    assert media_by_id["photo-message-1"]["media"] == {
        "url": "https://photo-stal-2.zdn.vn/photo.jpg",
        "thumbnail_url": "https://photo-stal-2.zdn.vn/photo-thumb.jpg",
        "width": 1280,
        "height": 720,
        "animated": False,
    }
    assert media_by_id["file-message-1"]["kind"] == "file"
    assert media_by_id["file-message-1"]["media"]["filename"] == "bao-gia-thang-8.pdf"
    assert media_by_id["file-message-1"]["media"]["mime_type"] == "application/pdf"
    assert media_by_id["file-message-1"]["media"]["size"] == 245760

    around_unread = client.get(
        "/api/v1/chat/messages",
        params={
            "account_id": account_id,
            "thread_id": "media-thread-4004",
            "around_event_id": media_conversation["first_unread_event_id"],
            "limit": 20,
        },
    )
    assert around_unread.status_code == 200
    assert "photo-message-1" in {item["id"] for item in around_unread.json()}

    marked_read = client.post(
        "/api/v1/chat/read-state",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "thread_id": "media-thread-4004",
        },
    )
    assert marked_read.status_code == 200
    assert (
        marked_read.json()["last_read_event_id"]
        == media_conversation["latest_event_id"]
    )
    conversations_after_read = client.get(
        "/api/v1/chat/conversations",
        params={"account_id": account_id},
    ).json()
    media_after_read = next(
        item
        for item in conversations_after_read
        if item["thread_id"] == "media-thread-4004"
    )
    assert media_after_read["unread_count"] == 0
    assert media_after_read["first_unread_event_id"] is None

    queued = client.post(
        "/api/v1/chat/messages",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "thread_id": "thread-1001",
            "thread_type": "user",
            "text": "Mình đã nhận được tin nhắn.",
        },
    )
    assert queued.status_code == 202
    assert queued.json()["message"]["status"] == "queued"
    with client.app.state.database.session_factory() as session:
        job = session.get(Job, queued.json()["job_id"])
        assert job.action == "sendMessage"
        assert job.priority == 50
        assert job.payload == {
            "message": {"text": "Mình đã nhận được tin nhắn."},
            "thread_id": "thread-1001",
            "thread_type": "user",
        }
        audit = session.scalar(
            select(AuditLog).where(AuditLog.action == "chat.message.enqueue")
        )
        assert audit is not None
        account = session.get(ZaloAccount, account_id)
        account.status = "error"
        session.commit()

    blocked = client.post(
        "/api/v1/chat/messages",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "thread_id": "thread-1001",
            "thread_type": "user",
            "text": "Tin này không được xếp hàng.",
        },
    )
    assert blocked.status_code == 409
    assert "admin" in blocked.json()["detail"].lower()


def test_chat_deduplicates_events_jobs_and_projects_stickers(client: TestClient):
    login(client)
    with client.app.state.database.session_factory() as session:
        account = ZaloAccount(
            name="Projection dedupe",
            cookie_cipher="encrypted-cookie",
            imei_cipher="encrypted-imei",
            listener_enabled=True,
            status="online",
            zalo_uid="self-uid",
        )
        session.add(account)
        session.flush()
        account_id = account.id
        duplicate_payload = {
            "message_id": "outer-old",
            "author_id": "friend-uid",
            "message": "Old duplicate",
            "message_object": {
                "msgId": "stable-100",
                "cliMsgId": "client-100",
                "msgType": "webchat",
                "dName": "Friend",
            },
            "thread_type": "USER",
        }
        session.add(
            Event(
                account_id=account_id,
                event_type="message.received",
                thread_id="thread-dedupe",
                payload=duplicate_payload,
            )
        )
        session.flush()
        newer_duplicate_payload = {
            **duplicate_payload,
            "message_id": "outer-new",
            "message": "Newest duplicate wins",
        }
        session.add_all(
            [
                Event(
                    account_id=account_id,
                    event_type="message.received",
                    thread_id="thread-dedupe",
                    payload=newer_duplicate_payload,
                ),
                Event(
                    account_id=account_id,
                    event_type="message.received",
                    thread_id="thread-dedupe",
                    payload={
                        "message_id": "sticker-200",
                        "author_id": "friend-uid",
                        "message": {
                            "id": 43521,
                            "catId": 11786,
                            "type": 3,
                            "extInfo": None,
                        },
                        "message_object": {
                            "msgId": "sticker-200",
                            "msgType": "chat.sticker",
                            "dName": "Friend",
                        },
                        "thread_type": "USER",
                    },
                ),
                Event(
                    account_id=account_id,
                    event_type="message.received",
                    thread_id="thread-dedupe",
                    payload={
                        "message_id": "deleted-300",
                        "author_id": "friend-uid",
                        "message": {},
                        "message_object": {
                            "msgId": "deleted-300",
                            "msgType": "chat.delete",
                        },
                        "thread_type": "USER",
                    },
                ),
                Event(
                    account_id=account_id,
                    event_type="message.received",
                    thread_id="thread-dedupe",
                    payload={
                        "message_id": "sent-500",
                        "author_id": "self-uid",
                        "message": "Sent from web",
                        "message_object": {
                            "msgId": "sent-500",
                            "msgType": "webchat",
                            "dName": "Admin",
                        },
                        "thread_type": "USER",
                    },
                ),
                Job(
                    account_id=account_id,
                    action="sendMessage",
                    payload={
                        "message": {"text": "Sent from web"},
                        "thread_id": "thread-dedupe",
                        "thread_type": "user",
                    },
                    status="succeeded",
                    result={"data": {"msgId": "sent-500"}},
                ),
            ]
        )
        session.commit()

    conversations = client.get(
        "/api/v1/chat/conversations",
        params={"account_id": account_id},
    )
    assert conversations.status_code == 200
    conversation = next(
        item
        for item in conversations.json()
        if item["thread_id"] == "thread-dedupe"
    )
    assert conversation["unread_count"] == 2

    response = client.get(
        "/api/v1/chat/messages",
        params={"account_id": account_id, "thread_id": "thread-dedupe"},
    )
    assert response.status_code == 200
    messages = response.json()
    assert [item["id"] for item in messages].count("stable-100") == 1
    duplicate = next(item for item in messages if item["id"] == "stable-100")
    assert duplicate["text"] == "Newest duplicate wins"
    assert not any(item["id"] == "deleted-300" for item in messages)
    assert not any(str(item["id"]).startswith("job:") for item in messages)

    sticker = next(item for item in messages if item["id"] == "sticker-200")
    assert sticker["kind"] == "sticker"
    assert sticker["media"]["sticker_id"] == "43521"
    assert sticker["media"]["thumbnail_url"] == (
        "https://zalo-api.zadn.vn/api/emoticon/sticker/"
        "webpc?eid=43521&size=130"
    )
    assert sticker["media"]["width"] == 130
    assert sticker["media"]["height"] == 130


def test_chat_contacts_reads_friend_cache_and_deduplicates_sync_jobs(client: TestClient):
    csrf = login(client)
    account_id = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Contacts account",
            "imei": "imei-contacts-12345",
            "cookies": {"zpw_sek": "contacts-secret"},
            "listener_enabled": True,
        },
    ).json()["id"]
    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        account.status = "online"
        session.add(
            Job(
                account_id=account_id,
                action="fetchAllFriends",
                payload={},
                status="succeeded",
                result=[
                    {
                        "userId": "friend-2",
                        "displayName": "Bảo An",
                        "avatar": "https://avatar.zdn.vn/friend-2.jpg",
                        "isOnline": True,
                    },
                    {
                        "uid": "friend-1",
                        "name": "An Bình",
                        "phoneNumber": "0900000001",
                        "isActive": False,
                    },
                    {
                        "userId": "friend-2",
                        "displayName": "Bảo An bản sao",
                    },
                ],
            )
        )
        session.commit()

    cached = client.get(
        "/api/v1/chat/contacts",
        params={"account_id": account_id},
    )
    assert cached.status_code == 200
    payload = cached.json()
    assert [item["user_id"] for item in payload["contacts"]] == ["friend-1", "friend-2"]
    friend_one = payload["contacts"][0]
    assert friend_one["name"] == "An Bình"
    assert friend_one["phone"] == "0900000001"
    assert friend_one["online"] is False
    friend_two = payload["contacts"][1]
    assert friend_two["avatar_url"].startswith("https://")
    assert friend_two["online"] is True
    assert payload["job"]["status"] == "succeeded"

    conversations_before_chat = client.get(
        "/api/v1/chat/conversations",
        params={"account_id": account_id},
    )
    assert conversations_before_chat.status_code == 200
    assert conversations_before_chat.json() == []

    sent = client.post(
        "/api/v1/chat/messages",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "thread_id": "friend-2",
            "thread_type": "user",
            "text": "Chào bạn từ danh bạ",
        },
    )
    assert sent.status_code == 202
    conversations_after_chat = client.get(
        "/api/v1/chat/conversations",
        params={"account_id": account_id},
    )
    assert conversations_after_chat.status_code == 200
    assert len(conversations_after_chat.json()) == 1
    assert conversations_after_chat.json()[0]["thread_id"] == "friend-2"
    assert conversations_after_chat.json()[0]["name"] == "Bảo An"
    assert conversations_after_chat.json()[0]["avatar_url"].startswith("https://")

    queued = client.post(
        "/api/v1/chat/contacts/sync",
        params={"account_id": account_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert queued.status_code == 202
    assert queued.json()["deduplicated"] is False
    with client.app.state.database.session_factory() as session:
        queued_job = session.get(Job, queued.json()["job_id"])
        assert queued_job.action == "fetchAllFriends"
        assert queued_job.status == "queued"

    deduplicated = client.post(
        "/api/v1/chat/contacts/sync",
        params={"account_id": account_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert deduplicated.status_code == 202
    assert deduplicated.json()["deduplicated"] is True
    assert deduplicated.json()["job_id"] == queued.json()["job_id"]


def test_chat_message_actions_and_custom_reaction_catalog(client: TestClient):
    csrf = login(client)
    account_id = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Interactive chat",
            "imei": "imei-interactive-12345",
            "cookies": {"zpw_sek": "interactive-secret"},
            "listener_enabled": True,
        },
    ).json()["id"]
    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        account.status = "online"
        account.zalo_uid = "self-uid"
        account.listener_enabled = True
        received = Event(
            account_id=account_id,
            event_type="message.received",
            thread_id="thread-actions",
            payload={
                "message_id": "101",
                "author_id": "friend-uid",
                "message": "Tin nhắn cần thao tác",
                "message_object": {
                    "msgId": "101",
                    "cliMsgId": "202",
                    "msgType": "webchat",
                    "uidFrom": "friend-uid",
                    "ts": "1720000000000",
                    "content": "Tin nhắn cần thao tác",
                    "dName": "Minh Anh",
                },
                "thread_type": "USER",
            },
        )
        mine = Event(
            account_id=account_id,
            event_type="message.received",
            thread_id="thread-actions",
            payload={
                "message_id": "303",
                "author_id": "self-uid",
                "message": "Tin nhắn của tôi",
                "message_object": {
                    "msgId": "303",
                    "cliMsgId": "404",
                    "msgType": "webchat",
                    "uidFrom": "self-uid",
                    "ts": "1720000001000",
                    "content": "Tin nhắn của tôi",
                    "dName": "Admin",
                },
                "thread_type": "USER",
            },
        )
        session.add_all([received, mine])
        session.commit()
        session.refresh(received)
        session.refresh(mine)
        received_id = received.id
        mine_id = mine.id

    catalog = client.get("/api/v1/chat/reactions/catalog").json()
    catalog.append(
        {
            "id": "custom-fire",
            "label": "Bùng cháy",
            "glyph": "🔥",
            "zalo_icon": "🔥",
            "r_type": -1,
            "source": 6,
            "mode": "local",
            "quick": True,
            "enabled": True,
            "verified": False,
            "fallback": "none",
            "locked": False,
        }
    )
    saved = client.put(
        "/api/v1/chat/reactions/catalog",
        headers={"X-CSRF-Token": csrf},
        json={"items": catalog},
    )
    assert saved.status_code == 200
    assert any(item["id"] == "custom-fire" for item in saved.json())

    local_reaction = client.post(
        "/api/v1/chat/reactions",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "event_id": received_id,
            "reaction_id": "custom-fire",
        },
    )
    assert local_reaction.status_code == 202
    assert local_reaction.json()["job_id"] is None

    messages = client.get(
        "/api/v1/chat/messages",
        params={"account_id": account_id, "thread_id": "thread-actions"},
    ).json()
    received_message = next(item for item in messages if item["event_id"] == received_id)
    assert received_message["message_ref"]["cli_msg_id"] == "202"
    assert received_message["reactions"] == [
        {
            "reaction_id": "custom-fire",
            "glyph": "🔥",
            "label": "Bùng cháy",
            "mode": "local",
            "count": 1,
            "mine": True,
        }
    ]

    native_reaction = client.post(
        "/api/v1/chat/reactions",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "event_id": received_id,
            "reaction_id": "like",
        },
    )
    assert native_reaction.status_code == 202
    with client.app.state.database.session_factory() as session:
        reaction_job = session.get(Job, native_reaction.json()["job_id"])
        assert reaction_job.action == "__chat_reaction__"
        assert reaction_job.payload["reactionIcon"] == "/-strong"
        assert reaction_job.payload["reactionType"] == 3

    reply = client.post(
        "/api/v1/chat/messages",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "thread_id": "thread-actions",
            "thread_type": "user",
            "text": "Đây là câu trả lời",
            "reply_event_id": received_id,
        },
    )
    assert reply.status_code == 202
    assert reply.json()["message"]["quote"]["message_id"] == "101"
    with client.app.state.database.session_factory() as session:
        reply_job = session.get(Job, reply.json()["job_id"])
        assert reply_job.action == "replyMessage"
        assert reply_job.payload["replyMsg"]["cliMsgId"] == "202"

    forwarded = client.post(
        "/api/v1/chat/forward",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "event_id": received_id,
            "targets": [
                {
                    "thread_id": "destination-group",
                    "thread_type": "group",
                    "name": "Nhóm nhận",
                }
            ],
        },
    )
    assert forwarded.status_code == 202
    with client.app.state.database.session_factory() as session:
        forward_job = session.get(Job, forwarded.json()["job_ids"][0])
        assert forward_job.action == "__chat_forward__"
        assert forward_job.payload["thread_id"] == "destination-group"
        assert forward_job.payload["_chat_context"]["forwarded"] is True

    undo = client.post(
        "/api/v1/chat/undo",
        headers={"X-CSRF-Token": csrf},
        json={"account_id": account_id, "event_id": mine_id},
    )
    assert undo.status_code == 202
    with client.app.state.database.session_factory() as session:
        undo_job = session.get(Job, undo.json()["job_id"])
        assert undo_job.action == "undoMessage"
        assert undo_job.payload["msgId"] == "303"
        assert undo_job.payload["cliMsgId"] == "404"


def test_chat_group_info_snapshot_search_data_and_sync_deduplication(
    client: TestClient,
):
    csrf = login(client)
    account_id = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Group directory",
            "imei": "imei-group-directory-12345",
            "cookies": {"zpw_sek": "group-directory-secret"},
            "listener_enabled": True,
        },
    ).json()["id"]
    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        account.status = "online"
        account.listener_enabled = True
        session.add(
            Event(
                account_id=account_id,
                event_type="group.metadata.synced",
                payload={
                    "groups": {
                        "group-directory-1": {
                            "name": "Tên nhóm dự phòng",
                            "avatar_url": "https://photo.zalo.me/fallback.jpg",
                            "member_count": 3,
                        }
                    }
                },
            )
        )
        session.add(
            Job(
                account_id=account_id,
                action="__chat_group_info__",
                payload={
                    "thread_id": "group-directory-1",
                    "groupId": "group-directory-1",
                },
                status="succeeded",
                result={
                    "thread_id": "group-directory-1",
                    "group": {
                        "thread_id": "group-directory-1",
                        "name": "Nhóm Điều Hành",
                        "description": "Trao đổi nội bộ",
                        "avatar_url": "https://photo.zalo.me/group.jpg",
                        "member_count": 3,
                        "owner_id": "owner-1",
                        "admin_ids": ["admin-2"],
                    },
                    "members": [
                        {
                            "user_id": "member-3",
                            "name": "Thành Viên",
                            "avatar_url": None,
                            "online": False,
                            "role": "member",
                        },
                        {
                            "user_id": "admin-2",
                            "name": "Quản Trị",
                            "avatar_url": "https://photo.zalo.me/admin.jpg",
                            "online": True,
                            "role": "admin",
                        },
                        {
                            "user_id": "owner-1",
                            "name": "Chủ Nhóm",
                            "avatar_url": "javascript:alert(1)",
                            "online": True,
                            "role": "owner",
                        },
                    ],
                },
                finished_at=utcnow(),
            )
        )
        session.commit()

    response = client.get(
        "/api/v1/chat/groups/group-directory-1",
        params={"account_id": account_id},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["group"]["name"] == "Nhóm Điều Hành"
    assert data["group"]["description"] == "Trao đổi nội bộ"
    assert data["group"]["member_count"] == 3
    assert [
        (item["user_id"], item["role"])
        for item in data["members"]
    ] == [
        ("owner-1", "owner"),
        ("admin-2", "admin"),
        ("member-3", "member"),
    ]
    assert data["members"][0]["avatar_url"] is None
    assert data["members"][1]["initials"] == "QT"

    queued = client.post(
        "/api/v1/chat/groups/group-directory-1/sync",
        params={"account_id": account_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert queued.status_code == 202
    assert queued.json()["deduplicated"] is False
    with client.app.state.database.session_factory() as session:
        job = session.get(Job, queued.json()["job_id"])
        assert job.action == "__chat_group_info__"
        assert job.payload["thread_id"] == "group-directory-1"

    deduplicated = client.post(
        "/api/v1/chat/groups/group-directory-1/sync",
        params={"account_id": account_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert deduplicated.status_code == 202
    assert deduplicated.json()["deduplicated"] is True
    assert deduplicated.json()["job_id"] == queued.json()["job_id"]


def test_chat_group_pins_snapshot_links_message_event_and_deduplicates_sync(
    client: TestClient,
):
    csrf = login(client)
    account_id = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Group pins",
            "imei": "imei-group-pins-12345",
            "cookies": {"zpw_sek": "group-pins-secret"},
            "listener_enabled": True,
        },
    ).json()["id"]
    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        account.status = "online"
        account.listener_enabled = True
        session.add(
            Event(
                account_id=account_id,
                event_type="message.received",
                thread_id="group-pins-1",
                payload={
                    "message_id": "global-pin-1",
                    "author_id": "member-3",
                    "message": "Thông báo họp lúc 14:00",
                    "message_object": {
                        "msgId": "global-pin-1",
                        "cliMsgId": "cli-pin-1",
                        "dName": "Thành Viên",
                    },
                    "thread_type": "GROUP",
                },
            )
        )
        session.add(
            Job(
                account_id=account_id,
                action="__chat_group_pins__",
                payload={"thread_id": "group-pins-1", "groupId": "group-pins-1"},
                status="succeeded",
                result={
                    "thread_id": "group-pins-1",
                    "pins": [
                        {
                            "pin_id": "pin-topic-1",
                            "message_id": "global-pin-1",
                            "cli_message_id": "cli-pin-1",
                            "author_name": "Thành Viên",
                            "title": "Thông báo họp lúc 14:00",
                            "message_type": "webchat",
                            "emoji": "📌",
                        },
                        {
                            "pin_id": "pin-topic-2",
                            "message_id": "not-in-event-log",
                            "title": "Tin cũ ngoài Event log",
                        },
                    ],
                    "count": 2,
                    "limit": 50,
                },
                finished_at=utcnow(),
            )
        )
        session.commit()

    response = client.get(
        "/api/v1/chat/groups/group-pins-1/pins",
        params={"account_id": account_id},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["thread_id"] == "group-pins-1"
    assert data["pins"][0]["event_id"] is not None
    assert data["pins"][0]["title"] == "Thông báo họp lúc 14:00"
    assert data["pins"][1]["event_id"] is None

    queued = client.post(
        "/api/v1/chat/groups/group-pins-1/pins/sync",
        params={"account_id": account_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert queued.status_code == 202
    assert queued.json()["deduplicated"] is False
    with client.app.state.database.session_factory() as session:
        job = session.get(Job, queued.json()["job_id"])
        assert job.action == "__chat_group_pins__"
        assert job.payload["thread_id"] == "group-pins-1"

    deduplicated = client.post(
        "/api/v1/chat/groups/group-pins-1/pins/sync",
        params={"account_id": account_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert deduplicated.status_code == 202
    assert deduplicated.json()["deduplicated"] is True
    assert deduplicated.json()["job_id"] == queued.json()["job_id"]


def test_chat_group_notes_snapshot_and_sync_deduplication(client: TestClient):
    csrf = login(client)
    account_id = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Group notes",
            "imei": "imei-group-notes-12345",
            "cookies": {"zpw_sek": "group-notes-secret"},
            "listener_enabled": True,
        },
    ).json()["id"]
    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        account.status = "online"
        account.listener_enabled = True
        session.add(
            Job(
                account_id=account_id,
                action="__chat_group_notes__",
                payload={"thread_id": "group-notes-1", "groupId": "group-notes-1"},
                status="succeeded",
                result={
                    "thread_id": "group-notes-1",
                    "notes": [
                        {
                            "note_id": "note-topic-1",
                            "content": "Lịch trực tuần này",
                            "creator_id": "owner-1",
                            "creator_name": "Chủ Nhóm",
                            "editor_id": "admin-2",
                            "editor_name": "Quản Trị",
                            "created_at": 1721000000000,
                            "emoji": "📝",
                            "color": "-14540254",
                        },
                        {
                            "note_id": "note-topic-1",
                            "content": "Bản trùng không được hiển thị",
                        },
                    ],
                },
                finished_at=utcnow(),
            )
        )
        session.commit()

    response = client.get(
        "/api/v1/chat/groups/group-notes-1/notes",
        params={"account_id": account_id},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["thread_id"] == "group-notes-1"
    assert len(data["notes"]) == 1
    assert data["notes"][0] == {
        "note_id": "note-topic-1",
        "content": "Lịch trực tuần này",
        "creator_id": "owner-1",
        "creator_name": "Chủ Nhóm",
        "editor_id": "admin-2",
        "editor_name": "Quản Trị",
        "created_at": 1721000000000,
        "updated_at": None,
        "emoji": "📝",
        "color": "-14540254",
    }

    queued = client.post(
        "/api/v1/chat/groups/group-notes-1/notes/sync",
        params={"account_id": account_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert queued.status_code == 202
    assert queued.json()["deduplicated"] is False
    with client.app.state.database.session_factory() as session:
        job = session.get(Job, queued.json()["job_id"])
        assert job.action == "__chat_group_notes__"
        assert job.payload["thread_id"] == "group-notes-1"

    deduplicated = client.post(
        "/api/v1/chat/groups/group-notes-1/notes/sync",
        params={"account_id": account_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert deduplicated.status_code == 202
    assert deduplicated.json()["deduplicated"] is True
    assert deduplicated.json()["job_id"] == queued.json()["job_id"]


def test_chat_group_polls_and_detail_are_read_only_and_deduplicated(client: TestClient):
    csrf = login(client)
    account_id = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Group polls",
            "imei": "imei-group-polls-12345",
            "cookies": {"zpw_sek": "group-polls-secret"},
            "listener_enabled": True,
        },
    ).json()["id"]
    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        account.status = "online"
        account.listener_enabled = True
        session.add(
            Job(
                account_id=account_id,
                action="__chat_group_polls__",
                payload={"thread_id": "group-polls-1", "groupId": "group-polls-1"},
                status="succeeded",
                result={
                    "thread_id": "group-polls-1",
                    "polls": [
                        {
                            "poll_id": "poll-1",
                            "question": "Chon lich hop",
                            "options": [
                                {"option_id": "opt-1", "label": "Thu Hai", "vote_count": 4},
                                {"option_id": "opt-2", "label": "Thu Ba", "vote_count": 2},
                            ],
                            "total_votes": 6,
                            "status": "active",
                            "allow_multiple": False,
                            "anonymous": True,
                            "results_hidden": False,
                        }
                    ],
                },
                finished_at=utcnow(),
            )
        )
        session.add(
            Job(
                account_id=account_id,
                action="__chat_poll_detail__",
                payload={
                    "thread_id": "group-polls-1",
                    "poll_id": "poll-1",
                },
                status="succeeded",
                result={
                    "thread_id": "group-polls-1",
                    "poll_id": "poll-1",
                    "poll": {
                        "poll_id": "poll-1",
                        "question": "Chon lich hop",
                        "options": [
                            {"option_id": "opt-1", "label": "Thu Hai", "vote_count": 5},
                            {"option_id": "opt-2", "label": "Thu Ba", "vote_count": 3},
                        ],
                        "total_votes": 8,
                        "status": "ended",
                        "allow_multiple": False,
                        "anonymous": True,
                    },
                },
                finished_at=utcnow(),
            )
        )
        session.commit()

    listed = client.get(
        "/api/v1/chat/groups/group-polls-1/polls",
        params={"account_id": account_id},
    )
    assert listed.status_code == 200
    assert listed.json()["polls"][0]["total_votes"] == 6
    assert listed.json()["polls"][0]["options"][0]["vote_count"] == 4

    detail = client.get(
        "/api/v1/chat/groups/group-polls-1/polls/poll-1",
        params={"account_id": account_id},
    )
    assert detail.status_code == 200
    assert detail.json()["poll"]["status"] == "ended"
    assert detail.json()["poll"]["total_votes"] == 8

    queued = client.post(
        "/api/v1/chat/groups/group-polls-1/polls/sync",
        params={"account_id": account_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert queued.status_code == 202
    assert queued.json()["deduplicated"] is False
    with client.app.state.database.session_factory() as session:
        job = session.get(Job, queued.json()["job_id"])
        assert job.action == "__chat_group_polls__"

    deduplicated = client.post(
        "/api/v1/chat/groups/group-polls-1/polls/sync",
        params={"account_id": account_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert deduplicated.status_code == 202
    assert deduplicated.json()["deduplicated"] is True

    detail_queued = client.post(
        "/api/v1/chat/groups/group-polls-1/polls/poll-1/sync",
        params={"account_id": account_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert detail_queued.status_code == 202
    assert detail_queued.json()["deduplicated"] is False
    detail_deduplicated = client.post(
        "/api/v1/chat/groups/group-polls-1/polls/poll-1/sync",
        params={"account_id": account_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert detail_deduplicated.status_code == 202
    assert detail_deduplicated.json()["deduplicated"] is True


def test_chat_group_management_requires_admin_confirmation_and_filters_members(
    client: TestClient,
):
    csrf = login(client)
    account_id = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Managed group account",
            "imei": "imei-managed-group-12345",
            "cookies": {"zpw_sek": "managed-group-secret"},
            "listener_enabled": True,
        },
    ).json()["id"]
    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        account.status = "online"
        account.listener_enabled = True
        session.add(
            Job(
                account_id=account_id,
                action="__chat_group_info__",
                payload={"thread_id": "managed-group-1", "groupId": "managed-group-1"},
                status="succeeded",
                result={
                    "group": {
                        "thread_id": "managed-group-1",
                        "name": "Nhóm Vận Hành",
                        "member_count": 2,
                    },
                    "members": [
                        {"user_id": "member-existing", "name": "Thành viên cũ"},
                        {"user_id": "member-owner", "name": "Chủ nhóm"},
                    ],
                },
                finished_at=utcnow(),
            )
        )
        session.commit()

    wrong_confirmation = client.post(
        "/api/v1/chat/groups/managed-group-1/rename",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "current_name": "Tên không đúng",
            "new_name": "Nhóm mới",
        },
    )
    assert wrong_confirmation.status_code == 422

    renamed = client.post(
        "/api/v1/chat/groups/managed-group-1/rename",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "current_name": "Nhóm Vận Hành",
            "new_name": "Nhóm Điều Phối",
        },
    )
    assert renamed.status_code == 202
    with client.app.state.database.session_factory() as session:
        rename_job = session.get(Job, renamed.json()["job_id"])
        assert rename_job.action == "changeGroupName"
        assert rename_job.payload["groupName"] == "Nhóm Điều Phối"
        assert rename_job.payload["groupId"] == "managed-group-1"
        assert rename_job.payload["_chat_context"]["previous_name"] == "Nhóm Vận Hành"
        assert rename_job.max_attempts == 1
        rename_job.status = "succeeded"
        rename_job.finished_at = utcnow()
        session.commit()

    invited = client.post(
        "/api/v1/chat/groups/managed-group-1/invite",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "user_ids": ["member-existing", "member-new", "member-new"],
        },
    )
    assert invited.status_code == 202
    assert invited.json()["invited_user_ids"] == ["member-new"]
    assert invited.json()["skipped_existing"] == 1
    with client.app.state.database.session_factory() as session:
        invite_job = session.get(Job, invited.json()["job_id"])
        assert invite_job.action == "addUsersToGroup"
        assert invite_job.payload["user_ids"] == ["member-new"]
        assert invite_job.payload["groupId"] == "managed-group-1"
        assert invite_job.max_attempts == 1
        audits = session.scalars(
            select(AuditLog).where(
                AuditLog.action.in_(
                    ("chat.group.rename.enqueue", "chat.group.members.invite.enqueue")
                )
            )
        ).all()
        assert {item.action for item in audits} == {
            "chat.group.rename.enqueue",
            "chat.group.members.invite.enqueue",
        }

    concurrent = client.post(
        "/api/v1/chat/groups/managed-group-1/invite",
        headers={"X-CSRF-Token": csrf},
        json={"account_id": account_id, "user_ids": ["member-another"]},
    )
    assert concurrent.status_code == 409

    api_key = client.post(
        "/api/v1/api-keys",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Operator group key", "scopes": ["read", "jobs:create"]},
    ).json()["api_key"]
    bypass_attempt = client.post(
        "/api/v1/actions/addUsersToGroup",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "account_id": account_id,
            "payload": {"user_ids": ["member-bypass"], "groupId": "managed-group-1"},
            "confirmation": "addUsersToGroup",
        },
    )
    assert bypass_attempt.status_code == 403

    with client.app.state.database.session_factory() as session:
        admin = session.scalar(select(User).where(User.username == "admin"))
        admin.role = "operator"
        session.commit()
    operator_chat = client.get("/chat")
    assert operator_chat.status_code == 200
    assert 'data-can-manage-groups="false"' in operator_chat.text
    assert 'id="chat-group-manage-open"' not in operator_chat.text


def test_chat_media_upload_signed_preview_and_queue(client: TestClient):
    csrf = login(client)
    blocked = client.post(
        "/api/v1/uploads",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("unsafe.exe", b"MZ", "application/octet-stream")},
    )
    assert blocked.status_code == 422

    upload_response = client.post(
        "/api/v1/uploads",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("minh-hoa.png", b"\x89PNG\r\n\x1a\nsample", "image/png")},
    )
    assert upload_response.status_code == 201
    upload = upload_response.json()
    assert upload["kind"] == "image"
    assert upload["content_type"] == "image/png"
    preview = client.get(upload["preview_url"])
    assert preview.status_code == 200
    assert preview.content == b"\x89PNG\r\n\x1a\nsample"
    assert preview.headers["content-type"] == "image/png"
    assert client.get(f"{upload['preview_url']}x").status_code == 403

    account_id = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Media sender",
            "imei": "imei-media-sender-12345",
            "cookies": {"zpw_sek": "media-secret"},
            "listener_enabled": True,
        },
    ).json()["id"]
    with client.app.state.database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        account.status = "online"
        account.listener_enabled = True
        session.commit()

    queued = client.post(
        "/api/v1/chat/media",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "thread_id": "media-destination",
            "thread_type": "group",
            "text": "Ảnh minh họa",
            "items": [
                {
                    "upload_token": upload["token"],
                    "kind": "image",
                    "name": upload["name"],
                    "content_type": upload["content_type"],
                    "size_bytes": upload["size_bytes"],
                    "width": 640,
                    "height": 480,
                }
            ],
        },
    )
    assert queued.status_code == 202
    assert queued.json()["message"]["kind"] == "image"
    assert queued.json()["message"]["media"]["items"][0]["filename"] == "minh-hoa.png"
    pending_projection = client.get(
        "/api/v1/chat/messages",
        params={
            "account_id": account_id,
            "thread_id": "media-destination",
        },
    ).json()
    assert [item["id"] for item in pending_projection] == [
        f"job:{queued.json()['job_id']}"
    ]
    with client.app.state.database.session_factory() as session:
        job = session.get(Job, queued.json()["job_id"])
        assert job.action == "__chat_media__"
        assert job.max_attempts == 1
        assert job.payload["items"][0]["upload_token"] == upload["token"]
        assert job.payload["items"][0]["url"].startswith(
            "http://localhost/media/outbound/"
        )
        audit = session.scalar(
            select(AuditLog).where(AuditLog.action == "chat.media.enqueue")
        )
        assert audit is not None
        account = session.get(ZaloAccount, account_id)
        account.zalo_uid = "self-media-uid"
        job.status = "succeeded"
        job.result = {
            "items": [
                {
                    "kind": "image",
                    "count": 1,
                    "result": {"msgId": "zalo-media-message-1"},
                }
            ],
            "count": 1,
        }
        session.add(
            Event(
                account_id=account_id,
                event_type="message.received",
                thread_id="media-destination",
                payload={
                    "message_id": "zalo-media-message-1",
                    "author_id": "self-media-uid",
                    "message": {
                        "href": "https://photo-stal-2.zdn.vn/sent-photo.jpg",
                        "thumb": "https://photo-stal-2.zdn.vn/sent-photo-thumb.jpg",
                        "params": '{"width": 640, "height": 480}',
                    },
                    "message_object": {
                        "msgId": "zalo-media-message-1",
                        "cliMsgId": "zalo-media-client-1",
                        "msgType": "chat.photo",
                        "dName": "Admin",
                    },
                    "thread_type": "GROUP",
                },
            )
        )
        session.commit()

    projected = client.get(
        "/api/v1/chat/messages",
        params={
            "account_id": account_id,
            "thread_id": "media-destination",
        },
    )
    assert projected.status_code == 200
    projected_media = [
        item
        for item in projected.json()
        if item["kind"] == "image" and item["mine"]
    ]
    assert len(projected_media) == 1
    assert projected_media[0]["id"] == "zalo-media-message-1"
    assert projected_media[0]["job_id"] is None
