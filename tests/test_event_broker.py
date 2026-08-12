from __future__ import annotations

import asyncio

from sqlalchemy.exc import SQLAlchemyError

from control_center.event_broker import EventBroker


def test_event_broker_recovers_after_transient_database_error(monkeypatch):
    class Database:
        def __init__(self):
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    class Broker(EventBroker):
        def __init__(self, database):
            super().__init__(database)
            self.fetch_calls = 0

        def _fetch(self):
            self.fetch_calls += 1
            if self.fetch_calls == 1:
                raise SQLAlchemyError("temporary database failure")
            return [
                {
                    "id": 7,
                    "account_id": "account-1",
                    "event_type": "message.received",
                    "thread_id": "thread-1",
                    "payload": {},
                    "created_at": "2026-08-06T00:00:00+00:00",
                }
            ]

    async def exercise():
        monkeypatch.setattr("control_center.event_broker.DATABASE_RETRY_INITIAL_SECONDS", 0.01)
        monkeypatch.setattr("control_center.event_broker.DATABASE_RETRY_MAX_SECONDS", 0.01)
        database = Database()
        broker = Broker(database)
        queue = broker.add_subscriber()
        task = asyncio.create_task(broker._tail())
        try:
            event = await asyncio.wait_for(queue.get(), timeout=1)
        finally:
            broker._stopping.set()
            await asyncio.wait_for(task, timeout=1)
        return database, broker, event

    database, broker, event = asyncio.run(exercise())
    assert database.dispose_calls == 1
    assert broker.fetch_calls >= 2
    assert broker.cursor == 7
    assert event["event_type"] == "message.received"
