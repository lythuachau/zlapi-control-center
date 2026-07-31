from __future__ import annotations

import threading
from contextlib import contextmanager
from types import SimpleNamespace

from sqlalchemy import select

import control_center.listener as listener_module
from control_center.db import Database
from control_center.listener import (
    ListenerHandle,
    ListenerSupervisor,
    record_listener_failure,
    store_listener_event,
)
from control_center.models import Event, Job, ZaloAccount, ZaloContactProfile


class DesiredAccountsDatabase:
    def __init__(self, desired: set[str]):
        self.desired = desired

    @contextmanager
    def session_factory(self):
        database = self

        class Session:
            def scalars(self, _query):
                return self

            def all(self):
                return [SimpleNamespace(id=value) for value in database.desired]

        yield Session()


def bare_listener_supervisor(database) -> ListenerSupervisor:
    supervisor = ListenerSupervisor.__new__(ListenerSupervisor)
    supervisor.database = database
    supervisor.secret_box = SimpleNamespace(
        decrypt_json=lambda _value: {"zpw_sek": "secret"},
        decrypt_text=lambda _value: "imei",
    )
    supervisor.stop_event = threading.Event()
    supervisor.handles = {}
    supervisor.lock = threading.Lock()
    supervisor.next_generation = 0
    return supervisor


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
        assert event.payload["failure_kind"] == "session_or_network"

    database.dispose()


def test_duplicate_listener_failure_does_not_request_new_credentials(settings):
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Duplicate listener test",
            cookie_cipher="encrypted-cookie",
            imei_cipher="encrypted-imei",
            listener_enabled=True,
            status="online",
        )
        session.add(account)
        session.commit()
        account_id = account.id

    record_listener_failure(
        database,
        account_id,
        RuntimeError(
            "Zalo closed this listener because another Web connection is active"
        ),
    )

    with database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        event = session.scalar(
            select(Event)
            .where(Event.account_id == account_id)
            .order_by(Event.id.desc())
        )
        assert account is not None
        assert account.listener_enabled is False
        assert event is not None
        assert event.payload["failure_kind"] == "duplicate_listener"
        assert event.payload["requires_admin_credentials_update"] is False

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
        jobs = session.scalars(
            select(Job).where(
                Job.account_id == account_id,
                Job.action == "__chat_profiles__",
            )
        ).all()
        assert len(jobs) == 1
        assert jobs[0].payload["user_ids"] == ["friend-200"]

    database.dispose()


def test_listener_coalesces_group_authors_into_one_profile_job(settings):
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Group profile batching",
            zalo_uid="self-uid",
            cookie_cipher="encrypted-cookie",
            imei_cipher="encrypted-imei",
            listener_enabled=True,
            status="online",
        )
        session.add(account)
        session.commit()
        account_id = account.id

    for index, author_id in enumerate(("member-1", "member-2", "member-1"), start=1):
        assert store_listener_event(
            database,
            account_id,
            "message.received",
            {
                "message_id": f"group-message-{index}",
                "author_id": author_id,
                "message": "Group interaction",
                "message_object": {"msgId": f"group-message-{index}"},
                "thread_type": "GROUP",
            },
            "group-100",
        )

    with database.session_factory() as session:
        jobs = session.scalars(
            select(Job).where(
                Job.account_id == account_id,
                Job.action == "__chat_profiles__",
            )
        ).all()
        assert len(jobs) == 1
        assert jobs[0].status == "queued"
        assert jobs[0].payload["user_ids"] == ["member-1", "member-2"]
        assert jobs[0].payload["_chat_context"]["profile_trigger"] == "interaction"

    database.dispose()


def test_listener_skips_fresh_complete_profile(settings):
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Fresh profile listener",
            zalo_uid="self-uid",
            cookie_cipher="encrypted-cookie",
            imei_cipher="encrypted-imei",
            listener_enabled=True,
            status="online",
        )
        session.add(account)
        session.commit()
        account_id = account.id
        session.add(
            ZaloContactProfile(
                account_id=account_id,
                user_id="known-member",
                name="Known Member",
                avatar_url="https://example.com/avatar.jpg",
                source="fetchUserInfo",
            )
        )
        session.commit()

    assert store_listener_event(
        database,
        account_id,
        "message.received",
        {
            "message_id": "known-message",
            "author_id": "known-member",
            "message": "Already cached",
            "message_object": {"msgId": "known-message"},
            "thread_type": "GROUP",
        },
        "group-200",
    )

    with database.session_factory() as session:
        assert session.scalar(
            select(Job).where(
                Job.account_id == account_id,
                Job.action == "__chat_profiles__",
            )
        ) is None

    database.dispose()


def test_reconcile_keeps_cancelled_generation_until_its_thread_exits(monkeypatch):
    database = DesiredAccountsDatabase(set())
    supervisor = bare_listener_supervisor(database)
    old_thread_release = threading.Event()
    old_thread = threading.Thread(
        target=old_thread_release.wait,
        kwargs={"timeout": 5},
        daemon=True,
    )
    old_cancel = threading.Event()
    old_handle = ListenerHandle(
        thread=old_thread,
        generation=1,
        cancel_event=old_cancel,
    )
    supervisor.next_generation = 1
    supervisor.handles["account-1"] = old_handle
    old_thread.start()

    supervisor.reconcile()
    assert old_cancel.is_set()
    assert supervisor.handles["account-1"] is old_handle

    # Rapidly enabling the account must not create a second generation while
    # the cancelled listener thread still exists.
    database.desired = {"account-1"}
    supervisor.reconcile()
    assert supervisor.handles["account-1"] is old_handle
    assert supervisor.next_generation == 1

    new_generation_started = threading.Event()

    def run_replacement(_account_id, _generation, _cancel_event):
        new_generation_started.set()

    monkeypatch.setattr(supervisor, "_run_account", run_replacement)
    old_thread_release.set()
    old_thread.join(timeout=1)
    supervisor.reconcile()

    assert new_generation_started.wait(1)
    assert supervisor.handles["account-1"].generation == 2


def test_cancelled_generation_cannot_listen_after_slow_login(monkeypatch):
    account = SimpleNamespace(
        cookie_cipher="cookie",
        imei_cipher="imei",
        user_agent="test-agent",
    )

    class AccountDatabase:
        @contextmanager
        def session_factory(self):
            yield SimpleNamespace(get=lambda _model, _account_id: account)

    supervisor = bare_listener_supervisor(AccountDatabase())
    constructor_started = threading.Event()
    release_constructor = threading.Event()
    listen_called = threading.Event()
    stopped = threading.Event()

    class BlockingClient:
        def __init__(self, **_kwargs):
            constructor_started.set()
            assert release_constructor.wait(2)

        def listen(self, **_kwargs):
            listen_called.set()

        def stopListening(self):
            stopped.set()

    monkeypatch.setattr(listener_module, "ManagedZaloClient", BlockingClient)

    cancel_event = threading.Event()
    account_thread = threading.Thread(
        target=supervisor._run_account,
        args=("account-1", 1, cancel_event),
        daemon=True,
    )
    supervisor.handles["account-1"] = ListenerHandle(
        thread=account_thread,
        generation=1,
        cancel_event=cancel_event,
    )
    supervisor.next_generation = 1
    account_thread.start()

    assert constructor_started.wait(1)
    cancel_event.set()
    release_constructor.set()
    account_thread.join(timeout=1)

    assert not account_thread.is_alive()
    assert not listen_called.is_set()
    assert stopped.is_set()
