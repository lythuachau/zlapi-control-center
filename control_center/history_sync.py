from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .models import Job

HISTORY_SOURCE = "history_backfill"
DEFAULT_HISTORY_LIMIT = 500
MAX_HISTORY_LIMIT = 1_000
HISTORY_SYNC_ACTION = "__chat_history_sync__"

ThreadKind = Literal["USER", "GROUP"]


class LastMessagesClient(Protocol):
    def getLastMsgs(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class HistorySyncEvent:
    """A normalized, persistence-ready event from Zalo's recent-message snapshot.

    ``dedupe_key`` is compatible with the live listener's key when Zalo supplies a
    message identifier.  ``occurred_at`` is the source timestamp and should be
    used as ``Event.created_at`` by the persistence caller when available.

    This module deliberately does not persist events.  Backfilled messages must
    not be passed through the live listener automation path, otherwise reconnects
    could replay automations for old mentions.
    """

    thread_id: str
    thread_type: ThreadKind
    payload: dict[str, Any]
    occurred_at: datetime | None
    dedupe_key: str


def queue_history_sync(
    session: Session,
    *,
    account_id: str,
    reason: str,
    created_by_id: str | None = None,
) -> Job:
    """Coalesce reconnect snapshots so one account never runs two at once."""

    existing = session.scalar(
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.action == HISTORY_SYNC_ACTION,
            Job.status.in_(("queued", "running")),
        )
        .order_by(desc(Job.created_at))
        .limit(1)
    )
    if existing is not None:
        return existing
    job = Job(
        account_id=account_id,
        action=HISTORY_SYNC_ACTION,
        payload={"reason": _text(reason) or "reconnect"},
        status="queued",
        priority=20,
        max_attempts=2,
        created_by_id=created_by_id,
    )
    session.add(job)
    session.flush()
    return job


def collect_recent_history(
    client: LastMessagesClient,
    *,
    account_uid: str,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> list[HistorySyncEvent]:
    """Fetch one bounded ``getLastMsgs`` snapshot and normalize its messages."""

    return normalize_last_messages(
        client.getLastMsgs(),
        account_uid=account_uid,
        limit=limit,
    )


def normalize_last_messages(
    response: Any,
    *,
    account_uid: str,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> list[HistorySyncEvent]:
    """Normalize the USER/GROUP arrays returned by ``getLastMsgs``.

    The result is sorted oldest-first, which lets a caller insert a snapshot in
    conversation order.  At most ``MAX_HISTORY_LIMIT`` events are returned even
    if an unsafe limit or unexpectedly large response is supplied.
    """

    normalized_uid = _text(account_uid)
    if not normalized_uid:
        raise ValueError("account_uid is required to normalize Zalo history")

    bounded_limit = _bounded_limit(limit)
    if bounded_limit == 0:
        return []

    container = _history_container(_jsonable(response))
    candidates: list[tuple[int, HistorySyncEvent]] = []
    source_index = 0
    for thread_type, key in (("USER", "msgs"), ("GROUP", "groupMsgs")):
        # Reading at most ``bounded_limit`` records from each collection keeps
        # malformed/oversized provider responses bounded before the final merge.
        for raw in _records(container.get(key), bounded_limit):
            normalized = normalize_history_message(
                raw,
                thread_type=thread_type,
                account_uid=normalized_uid,
            )
            if normalized is not None:
                candidates.append((source_index, normalized))
            source_index += 1

    candidates.sort(key=_event_sort_key)
    # Keep the newest portion when both source collections exceed the global
    # limit, while retaining oldest-first order inside the returned portion.
    return [event for _, event in candidates[-bounded_limit:]]


def normalize_history_message(
    raw_message: Any,
    *,
    thread_type: ThreadKind,
    account_uid: str,
) -> HistorySyncEvent | None:
    """Normalize one provider message, or return ``None`` for an unknown thread."""

    raw = _jsonable(raw_message)
    if not isinstance(raw, dict):
        return None

    normalized_uid = _text(account_uid)
    if not normalized_uid:
        raise ValueError("account_uid is required to normalize Zalo history")

    uid_from = _text(raw.get("uidFrom"))
    id_to = _text(raw.get("idTo"))
    sender_is_self = uid_from == "0" or uid_from == normalized_uid
    author_id = normalized_uid if uid_from == "0" else uid_from

    if thread_type == "GROUP":
        thread_id = id_to
    elif thread_type == "USER":
        # Zalo encodes messages authored by this account with uidFrom=0 in the
        # preload snapshot.  For those messages the peer is idTo.  Incoming
        # messages belong to the uidFrom peer regardless of idTo.
        thread_id = id_to if sender_is_self else uid_from
    else:
        raise ValueError(f"Unsupported thread_type: {thread_type}")

    if not thread_id or thread_id == "0":
        return None

    message_id = _first_text(
        raw.get("msgId"),
        raw.get("messageId"),
        raw.get("globalMsgId"),
        raw.get("cliMsgId"),
        raw.get("clientMsgId"),
    )
    source_timestamp = raw.get("ts")
    payload: dict[str, Any] = {
        "message_id": message_id,
        "author_id": author_id,
        "message": raw.get("content"),
        "message_object": raw,
        "thread_type": thread_type,
        "source": HISTORY_SOURCE,
        "timestamp": source_timestamp,
    }
    occurred_at = history_timestamp(source_timestamp)
    return HistorySyncEvent(
        thread_id=thread_id,
        thread_type=thread_type,
        payload=payload,
        occurred_at=occurred_at,
        dedupe_key=stable_history_dedupe_key(thread_id, payload),
    )


def stable_history_dedupe_key(thread_id: str, payload: Mapping[str, Any]) -> str:
    """Return a stable Event dedupe key, including a no-message-id fallback.

    For provider-assigned IDs the formula intentionally matches
    ``listener.listener_event_dedupe_key``.  The fallback hashes canonical JSON,
    so it remains deterministic across dictionary key ordering and process restarts.
    """

    normalized_thread_id = _text(thread_id)
    if not normalized_thread_id:
        raise ValueError("thread_id is required to build a history dedupe key")

    message_object = payload.get("message_object")
    message_object = message_object if isinstance(message_object, Mapping) else {}
    identity = _first_text(
        message_object.get("msgId"),
        message_object.get("messageId"),
        message_object.get("globalMsgId"),
        payload.get("message_id"),
        message_object.get("cliMsgId"),
        message_object.get("clientMsgId"),
    )
    if identity is None:
        fingerprint_source = {
            "version": 1,
            "thread_id": normalized_thread_id,
            "thread_type": payload.get("thread_type"),
            "author_id": payload.get("author_id"),
            "timestamp": payload.get("timestamp") or message_object.get("ts"),
            "message": payload.get("message"),
            "message_object": message_object,
        }
        canonical = json.dumps(
            _jsonable(fingerprint_source),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        identity = f"history-fallback-v1:{hashlib.sha256(canonical).hexdigest()}"

    raw_key = f"{normalized_thread_id}\x1f{identity}".encode()
    return hashlib.sha256(raw_key).hexdigest()


def history_timestamp(value: Any) -> datetime | None:
    """Parse a Zalo seconds/milliseconds/microseconds Unix timestamp as UTC."""

    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if timestamp <= 0:
        return None
    absolute = abs(timestamp)
    if absolute >= 100_000_000_000_000:
        timestamp /= 1_000_000
    elif absolute >= 100_000_000_000:
        timestamp /= 1_000
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _event_sort_key(item: tuple[int, HistorySyncEvent]) -> tuple[int, datetime, int]:
    source_index, event = item
    if event.occurred_at is None:
        return (1, datetime.max.replace(tzinfo=UTC), source_index)
    return (0, event.occurred_at, source_index)


def _bounded_limit(value: int) -> int:
    if isinstance(value, bool):
        raise TypeError("limit must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TypeError("limit must be an integer") from exc
    if parsed <= 0:
        return 0
    return min(parsed, MAX_HISTORY_LIMIT)


def _history_container(value: Any) -> dict[str, Any]:
    current = value
    for _ in range(4):
        if not isinstance(current, dict):
            return {}
        if "msgs" in current or "groupMsgs" in current:
            return current
        nested = next(
            (
                current.get(key)
                for key in ("data", "result", "response")
                if isinstance(current.get(key), dict)
            ),
            None,
        )
        if nested is None:
            return {}
        current = nested
    return {}


def _records(value: Any, limit: int) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        records: Iterable[Any] = value
    elif isinstance(value, tuple):
        records = value
    elif isinstance(value, dict):
        if _looks_like_message(value):
            records = (value,)
        else:
            records = value.values()
    else:
        return
    for index, record in enumerate(records):
        if index >= limit:
            break
        normalized = _jsonable(record)
        if isinstance(normalized, dict):
            yield normalized


def _looks_like_message(value: Mapping[str, Any]) -> bool:
    return any(key in value for key in ("msgId", "messageId", "uidFrom", "idTo", "content"))


def _first_text(*values: Any) -> str | None:
    return next((text for value in values if (text := _text(value)) is not None), None)


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "value"):
        return _jsonable(value.value)
    if hasattr(value, "__dict__"):
        return {
            key: _jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)
