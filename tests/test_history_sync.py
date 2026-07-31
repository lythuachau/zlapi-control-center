from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from munch import DefaultMunch

from control_center.history_sync import (
    HISTORY_SOURCE,
    MAX_HISTORY_LIMIT,
    collect_recent_history,
    history_timestamp,
    normalize_history_message,
    normalize_last_messages,
    stable_history_dedupe_key,
)


def test_normalizes_incoming_and_outgoing_user_threads():
    events = normalize_last_messages(
        {
            "msgs": [
                {
                    "msgId": "incoming-1",
                    "uidFrom": "friend-1",
                    "idTo": "self-1",
                    "content": "received",
                    "ts": "1785456000000",
                },
                {
                    "msgId": "outgoing-1",
                    "uidFrom": "0",
                    "idTo": "friend-2",
                    "content": "sent",
                    "ts": "1785456001000",
                },
                {
                    "msgId": "outgoing-2",
                    "uidFrom": "self-1",
                    "idTo": "friend-3",
                    "content": "sent with explicit uid",
                    "ts": "1785456002000",
                },
            ]
        },
        account_uid="self-1",
    )

    assert [(event.thread_id, event.payload["author_id"]) for event in events] == [
        ("friend-1", "friend-1"),
        ("friend-2", "self-1"),
        ("friend-3", "self-1"),
    ]
    assert all(event.thread_type == "USER" for event in events)
    assert all(event.payload["thread_type"] == "USER" for event in events)
    assert all(event.payload["source"] == HISTORY_SOURCE for event in events)
    assert events[0].payload["message_object"]["content"] == "received"


def test_normalizes_group_thread_and_self_author():
    event = normalize_history_message(
        DefaultMunch.fromDict(
            {
                "globalMsgId": 991,
                "uidFrom": 0,
                "idTo": 442,
                "content": {"title": "photo"},
                "msgType": "chat.photo",
                "ts": 1_785_456_000_000,
            },
            None,
        ),
        thread_type="GROUP",
        account_uid="123",
    )

    assert event is not None
    assert event.thread_id == "442"
    assert event.payload == {
        "message_id": "991",
        "author_id": "123",
        "message": {"title": "photo"},
        "message_object": {
            "globalMsgId": 991,
            "uidFrom": 0,
            "idTo": 442,
            "content": {"title": "photo"},
            "msgType": "chat.photo",
            "ts": 1_785_456_000_000,
        },
        "thread_type": "GROUP",
        "source": HISTORY_SOURCE,
        "timestamp": 1_785_456_000_000,
    }
    assert event.occurred_at == datetime(2026, 7, 31, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "thread_type"),
    [
        ({"uidFrom": "0", "content": "missing peer"}, "USER"),
        ({"uidFrom": "friend", "idTo": "0", "content": "missing group"}, "GROUP"),
        ({"content": "missing everything"}, "USER"),
    ],
)
def test_skips_messages_without_a_known_thread(raw, thread_type):
    assert (
        normalize_history_message(
            raw,
            thread_type=thread_type,
            account_uid="self",
        )
        is None
    )


def test_explicit_message_id_dedupe_matches_live_listener_formula():
    payload = {
        "message_id": "fallback-id",
        "message_object": {"msgId": "provider-id"},
    }
    expected = hashlib.sha256(b"thread-1\x1fprovider-id").hexdigest()

    assert stable_history_dedupe_key("thread-1", payload) == expected


def test_fallback_dedupe_is_stable_across_mapping_order_and_changes_with_content():
    first = {
        "message": {"url": "https://example.test/a", "title": "A"},
        "message_object": {
            "uidFrom": "friend",
            "idTo": "self",
            "ts": "1234000",
            "content": {"height": 20, "width": 10},
        },
        "thread_type": "USER",
        "author_id": "friend",
        "timestamp": "1234000",
    }
    reordered = {
        "timestamp": "1234000",
        "author_id": "friend",
        "thread_type": "USER",
        "message_object": {
            "content": {"width": 10, "height": 20},
            "ts": "1234000",
            "idTo": "self",
            "uidFrom": "friend",
        },
        "message": {"title": "A", "url": "https://example.test/a"},
    }
    changed = {**first, "message": {"url": "https://example.test/b", "title": "B"}}

    assert stable_history_dedupe_key("friend", first) == stable_history_dedupe_key(
        "friend", reordered
    )
    assert stable_history_dedupe_key("friend", first) != stable_history_dedupe_key(
        "friend", changed
    )


def test_normalization_is_bounded_globally():
    response = {
        "msgs": [
            {
                "msgId": f"u-{index}",
                "uidFrom": f"user-{index}",
                "idTo": "self",
                "ts": (index + 1) * 1_000,
            }
            for index in range(10)
        ],
        "groupMsgs": [
            {
                "msgId": f"g-{index}",
                "uidFrom": f"member-{index}",
                "idTo": "group",
                "ts": (index + 11) * 1_000,
            }
            for index in range(10)
        ],
    }

    events = normalize_last_messages(response, account_uid="self", limit=3)

    assert [event.payload["message_id"] for event in events] == ["g-0", "g-1", "g-2"]
    assert len(events) == 3
    assert MAX_HISTORY_LIMIT == 1_000


def test_collect_calls_get_last_messages_once_and_accepts_wrapped_munch():
    class FakeClient:
        calls = 0

        def getLastMsgs(self):
            self.calls += 1
            return DefaultMunch.fromDict(
                {
                    "data": {
                        "msgs": {
                            "peer": {
                                "msgId": "m-1",
                                "uidFrom": "peer",
                                "idTo": "self",
                                "content": "hello",
                                "ts": 1_785_456_000,
                            }
                        }
                    }
                },
                None,
            )

    client = FakeClient()
    events = collect_recent_history(client, account_uid="self")

    assert client.calls == 1
    assert len(events) == 1
    assert events[0].thread_id == "peer"
    assert events[0].payload["message"] == "hello"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1_785_456_000, datetime(2026, 7, 31, 0, 0, tzinfo=UTC)),
        (1_785_456_000_000, datetime(2026, 7, 31, 0, 0, tzinfo=UTC)),
        (1_785_456_000_000_000, datetime(2026, 7, 31, 0, 0, tzinfo=UTC)),
        ("not-a-timestamp", None),
        (None, None),
        (-1, None),
    ],
)
def test_history_timestamp_supports_provider_units(value, expected):
    assert history_timestamp(value) == expected
