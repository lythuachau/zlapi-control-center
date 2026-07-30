from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from sqlalchemy import func, select

from .db import Database
from .models import Event


class EventBroker:
    def __init__(self, database: Database):
        self.database = database
        self.cursor = 0
        self.subscribers: set[asyncio.Queue] = set()
        self.task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        self.cursor = await asyncio.to_thread(self._latest_cursor)
        self.task = asyncio.create_task(self._tail(), name="event-broker")

    async def stop(self) -> None:
        self._stopping.set()
        if self.task:
            await self.task

    def _latest_cursor(self) -> int:
        with self.database.session_factory() as session:
            return int(session.scalar(select(func.max(Event.id))) or 0)

    def _fetch(self) -> list[dict]:
        with self.database.session_factory() as session:
            rows = session.scalars(
                select(Event).where(Event.id > self.cursor).order_by(Event.id).limit(500)
            ).all()
            return [
                {
                    "id": event.id,
                    "account_id": event.account_id,
                    "event_type": event.event_type,
                    "thread_id": event.thread_id,
                    "payload": event.payload,
                    "created_at": event.created_at.isoformat(),
                }
                for event in rows
            ]

    async def _tail(self) -> None:
        while not self._stopping.is_set():
            events = await asyncio.to_thread(self._fetch)
            for event in events:
                self.cursor = max(self.cursor, event["id"])
                for queue in tuple(self.subscribers):
                    if queue.full():
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    queue.put_nowait(event)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=1)
            except TimeoutError:
                continue

    async def subscribe(self) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self.subscribers.discard(queue)

    def add_subscriber(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.subscribers.add(queue)
        return queue

    def remove_subscriber(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)
