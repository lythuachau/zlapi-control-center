from __future__ import annotations

from sqlalchemy import select

from control_center.db import Database
from control_center.listener import record_listener_failure, store_listener_event
from control_center.models import Event, ZaloAccount


def test_listener_failure_disables_account_until_admin_update(settings):
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Listener test",
            cookie_cipher="encrypted-cookie",
            imei_cipher="encrypted-imei",
            listener_enabled=True,
            status="online",
        )
        session.add(account)
        session.commit()
        account_id = account.id

    record_listener_failure(database, account_id, RuntimeError("session expired"))

    with database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        event = session.scalar(
            select(Event)
            .where(Event.account_id == account_id)
            .order_by(Event.id.desc())
        )
        assert account is not None
        assert account.status == "error"
        assert account.listener_enabled is False
        assert account.last_error == "session expired"
        assert event is not None
        assert event.event_type == "listener.error"
        assert event.payload["listener_disabled"] is True
        assert event.payload["requires_admin_credentials_update"] is True

    database.dispose()


def test_listener_stores_each_message_identity_once(settings):
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Listener dedupe test",
            cookie_cipher="encrypted-cookie",
            imei_cipher="encrypted-imei",
            listener_enabled=True,
            status="online",
        )
        session.add(account)
        session.commit()
        account_id = account.id

    payload = {
        "message_id": "message-100",
        "author_id": "friend-200",
        "message": "Only store me once",
        "message_object": {
            "msgId": "message-100",
            "cliMsgId": "client-100",
            "msgType": "webchat",
        },
        "thread_type": "USER",
    }
    assert store_listener_event(
        database,
        account_id,
        "message.received",
        payload,
        "thread-300",
    )
    assert not store_listener_event(
        database,
        account_id,
        "message.received",
        payload,
        "thread-300",
    )

    with database.session_factory() as session:
        events = session.scalars(
            select(Event).where(
                Event.account_id == account_id,
                Event.event_type == "message.received",
            )
        ).all()
        assert len(events) == 1
        assert events[0].dedupe_key is not None
        assert len(events[0].dedupe_key) == 64

    database.dispose()
