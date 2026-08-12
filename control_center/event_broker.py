from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SATimeoutError

from .db import Database
from .models import Event

logger = logging.getLogger("zlapi.event_broker")
DATABASE_RETRY_INITIAL_SECONDS = 1.0
DATABASE_RETRY_MAX_SECONDS = 30.0


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
        return self.fetch_since(self.cursor)

    def fetch_since(self, cursor: int, limit: int = 500) -> list[dict]:
        with self.database.session_factory() as session:
            rows = session.scalars(
                select(Event).where(Event.id > cursor).order_by(Event.id).limit(limit)
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
        retry_delay = DATABASE_RETRY_INITIAL_SECONDS
        while not self._stopping.is_set():
            try:
                events = await asyncio.to_thread(self._fetch)
            except asyncio.CancelledError:
                raise
            except SATimeoutError as exc:
                # Pool contention is transient and disposing here could close
                # connections still in use by request handlers.
                logger.warning(
                    "Event broker database pool busy (%s); retrying in %.1f seconds",
                    type(exc).__name__,
                    retry_delay,
                )
                await self._wait_before_retry(retry_delay)
                retry_delay = min(DATABASE_RETRY_MAX_SECONDS, retry_delay * 2)
                continue
            except SQLAlchemyError as exc:
                # A broken/stale database connection must not terminate the
                # broker task; the web process can continue serving reads while
                # the broker reconnects and resumes from the same cursor.
                self.database.dispose()
                logger.warning(
                    "Event broker database unavailable (%s); retrying in %.1f seconds",
                    type(exc).__name__,
                    retry_delay,
                )
                await self._wait_before_retry(retry_delay)
                retry_delay = min(DATABASE_RETRY_MAX_SECONDS, retry_delay * 2)
                continue
            retry_delay = DATABASE_RETRY_INITIAL_SECONDS
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

    async def _wait_before_retry(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=delay)
        except TimeoutError:
            pass

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
