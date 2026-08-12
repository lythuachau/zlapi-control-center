from __future__ import annotations

import asyncio
from types import SimpleNamespace

from control_center.api import event_stream


def test_event_stream_replays_events_after_last_event_id():
    class Broker:
        def __init__(self):
            self.queue = asyncio.Queue()
            self.removed = None

        def add_subscriber(self):
            return self.queue

        def remove_subscriber(self, queue):
            self.removed = queue

        def fetch_since(self, cursor):
            assert cursor == 41
            return [
                {
                    "id": 42,
                    "account_id": "account-1",
                    "event_type": "message.received",
                    "thread_id": "thread-replay",
                    "payload": {"message_id": "message-2"},
                    "created_at": "2026-08-06T00:00:00+00:00",
                }
            ]

    class Request:
        headers = {"last-event-id": "41"}
        query_params = {}

        def __init__(self, broker):
            self.app = SimpleNamespace(state=SimpleNamespace(event_broker=broker))
            self.disconnect_checks = 0

        async def is_disconnected(self):
            self.disconnect_checks += 1
            return self.disconnect_checks > 0

    async def exercise():
        broker = Broker()
        request = Request(broker)
        response = await event_stream(request)
        chunks = [chunk async for chunk in response.body_iterator]
        return broker, "".join(chunks)

    broker, body = asyncio.run(exercise())
    assert "id: 42" in body
    assert "message-2" in body
    assert broker.removed is broker.queue
