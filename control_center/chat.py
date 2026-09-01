from __future__ import annotations

import json
import mimetypes
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from .models import ConversationRead, Event, Job, ZaloAccount, utc_isoformat
from .profiles import cached_profiles
from .reactions import reaction_catalog, reaction_presentation

NON_MESSAGE_TYPES = {"chat.delete", "chat.reaction", "chat.undo"}
OUTBOUND_CHAT_ACTIONS = {
    "sendMessage",
    "replyMessage",
    "__chat_forward__",
    "__chat_media__",
    "__chat_rich_media__",
}
MEDIA_LABELS = {
    "gif": "[GIF]",
    "contact": "[Danh thiáº¿p]",
    "image": "[Hình ảnh]",
    "video": "[Video]",
    "audio": "[Tin nhắn thoại]",
    "file": "[Tệp đính kèm]",
    "sticker": "[Sticker]",
    "poll": "[Bình chọn]",
    "link": "[Liên kết]",
}


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _mapping_value(mapping: Any, keys: Iterable[str]) -> str | None:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = _text(mapping.get(key))
        if value:
            return value
    return None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def _json_mapping(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_url(value: Any) -> str | None:
    candidate = _text(value)
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _message_object(payload: dict) -> dict:
    value = payload.get("message_object")
    return value if isinstance(value, dict) else {}


def message_reference(payload: dict) -> dict:
    message_object = _message_object(payload)
    return {
        "msg_id": _mapping_value(
            message_object,
            ("msgId", "messageId", "globalMsgId"),
        )
        or _text(payload.get("message_id")),
        "cli_msg_id": _mapping_value(
            message_object,
            ("cliMsgId", "clientMsgId"),
        ),
        "msg_type": _text(message_object.get("msgType")) or "webchat",
        "uid_from": _mapping_value(
            message_object,
            ("uidFrom", "userId", "uin"),
        )
        or _text(payload.get("author_id")),
        "ts": _text(message_object.get("ts")),
    }


def _result_message_ids(value: Any) -> set[str]:
    identities: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = key.replace("_", "").lower()
            if normalized_key in {
                "msgid",
                "messageid",
                "globalmsgid",
                "climsgid",
                "clientmsgid",
            }:
                identity = _text(item)
                if identity:
                    identities.add(identity)
            if isinstance(item, (dict, list, tuple)):
                identities.update(_result_message_ids(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            identities.update(_result_message_ids(item))
    return identities


def _event_message_ids(event: Event) -> set[str]:
    payload = event.payload or {}
    message_object = _message_object(payload)
    reference = message_reference(payload)
    return {
        str(value)
        for value in (
            reference.get("msg_id"),
            reference.get("cli_msg_id"),
            payload.get("message_id"),
            message_object.get("msgId"),
            message_object.get("messageId"),
            message_object.get("globalMsgId"),
            message_object.get("cliMsgId"),
            message_object.get("clientMsgId"),
        )
        if value not in (None, "")
    }


def _job_has_listener_echo(
    job: Job,
    listener_message_ids: set[str],
) -> bool:
    return (
        job.action in OUTBOUND_CHAT_ACTIONS
        and job.status == "succeeded"
        and bool(_result_message_ids(job.result) & listener_message_ids)
    )


def _deduplicated_projectable_events(events: Iterable[Event]) -> list[Event]:
    seen_identities: set[str] = set()
    deduplicated: list[Event] = []
    for event in sorted(events, key=lambda item: item.id, reverse=True):
        if not is_projectable_message(event.payload or {}):
            continue
        identities = _event_message_ids(event)
        if identities and identities & seen_identities:
            continue
        seen_identities.update(identities)
        deduplicated.append(event)
    return deduplicated


def message_quote(payload: dict) -> dict | None:
    quote = _message_object(payload).get("quote")
    if not isinstance(quote, dict) or not quote:
        return None
    attachment = _json_mapping(quote.get("attach"))
    quote_text = _text(quote.get("msg"))
    if not quote_text and attachment:
        quote_text = (
            _mapping_value(attachment, ("title", "description", "fileName"))
            or "[Nội dung đính kèm]"
        )
    return {
        "message_id": _mapping_value(
            quote,
            ("globalMsgId", "msgId", "cliMsgId"),
        ),
        "cli_msg_id": _text(quote.get("cliMsgId")),
        "author_id": _text(quote.get("ownerId")),
        "author_name": _text(quote.get("fromD")) or "Tin nhắn",
        "text": quote_text or "[Nội dung không phải văn bản]",
        "msg_type": _text(quote.get("cliMsgType")) or "webchat",
    }


def _message_content(payload: dict) -> dict:
    direct = payload.get("message")
    if isinstance(direct, dict):
        return direct
    content = _message_object(payload).get("content")
    return content if isinstance(content, dict) else {}


def message_type(payload: dict) -> str:
    value = _message_object(payload).get("msgType") or payload.get("msg_type")
    return str(value or "webchat").lower()


def is_projectable_message(payload: dict) -> bool:
    return message_type(payload) not in NON_MESSAGE_TYPES


def message_media(payload: dict) -> tuple[str, dict | None]:
    msg_type = message_type(payload)
    content = _message_content(payload)
    params = _json_mapping(content.get("params"))
    webp = _json_mapping(params.get("webp"))
    href = (
        _safe_url(content.get("href"))
        or _safe_url(content.get("url"))
        or _safe_url(params.get("href"))
    )
    thumb = (
        _safe_url(content.get("thumb"))
        or _safe_url(content.get("thumbnail"))
        or _safe_url(params.get("thumb"))
        or _safe_url(params.get("thumbnail"))
        or _safe_url(params.get("thumbnailUrl"))
    )

    if msg_type in {"chat.photo", "chat.gif"}:
        url = _safe_url(webp.get("url")) or href or thumb
        media = {
            "url": url,
            "thumbnail_url": thumb or url,
            "width": _number(params.get("width") or content.get("width")),
            "height": _number(params.get("height") or content.get("height")),
            "animated": msg_type == "chat.gif",
        }
        return (
            "gif" if msg_type == "chat.gif" else "image",
            {key: value for key, value in media.items() if value is not None},
        )

    if msg_type == "chat.video.msg":
        media = {
            "url": href
            or _safe_url(params.get("videoUrl"))
            or _safe_url(params.get("url")),
            "thumbnail_url": thumb,
            "width": _number(
                params.get("video_width")
                or params.get("video_original_width")
                or params.get("width")
                or content.get("width")
            ),
            "height": _number(
                params.get("video_height")
                or params.get("video_original_height")
                or params.get("height")
                or content.get("height")
            ),
            "duration_ms": _number(
                params.get("duration") or params.get("durationMs")
            ),
            "size": _number(
                params.get("fileSize")
                or params.get("size")
                or content.get("fileSize")
            ),
        }
        return "video", {key: value for key, value in media.items() if value is not None}

    if msg_type == "chat.voice":
        media = {
            "url": href
            or _safe_url(params.get("url"))
            or _safe_url(params.get("voiceUrl"))
            or _safe_url(params.get("audioUrl")),
            "duration_ms": _number(
                params.get("duration") or params.get("durationMs")
            ),
            "size": _number(
                params.get("fileSize")
                or params.get("size")
                or content.get("fileSize")
            ),
        }
        return "audio", {key: value for key, value in media.items() if value is not None}

    if msg_type == "share.file":
        filename = _mapping_value(
            content,
            ("title", "fileName", "filename", "name", "description"),
        ) or _mapping_value(params, ("fileName", "filename", "name"))
        extension = _mapping_value(params, ("fileExt", "extension", "ext"))
        if not filename and extension:
            filename = f"Tệp đính kèm.{extension.lstrip('.')}"
        mime_type = mimetypes.guess_type(filename or "")[0]
        media = {
            "url": href
            or _safe_url(params.get("downloadUrl"))
            or _safe_url(params.get("url"))
            or _safe_url(params.get("fdata")),
            "thumbnail_url": thumb,
            "filename": filename or "Tệp đính kèm",
            "extension": extension,
            "mime_type": mime_type,
            "size": _number(
                params.get("fileSize")
                or params.get("totalSize")
                or content.get("fileSize")
            ),
        }
        return "file", {key: value for key, value in media.items() if value is not None}

    if msg_type == "chat.sticker":
        ext_info = _json_mapping(content.get("extInfo"))
        sticker_id = _text(content.get("id"))
        sticker_url = (
            f"https://zalo-api.zadn.vn/api/emoticon/sticker/webpc?eid={sticker_id}&size=130"
            if sticker_id and sticker_id.isdigit()
            else None
        )
        media = {
            "url": _safe_url(ext_info.get("url"))
            or _safe_url(ext_info.get("href"))
            or href
            or sticker_url,
            "thumbnail_url": sticker_url,
            "width": _number(ext_info.get("width")) or 130,
            "height": _number(ext_info.get("height")) or 130,
            "sticker_id": sticker_id,
            "category_id": _text(content.get("catId")),
            "sticker_type": _number(content.get("type")),
        }
        return "sticker", {key: value for key, value in media.items() if value is not None}

    if msg_type == "group.poll":
        poll_info = _json_mapping(content.get("pollInfo"))
        media = {
            "question": _mapping_value(params, ("question", "title"))
            or _mapping_value(content, ("title", "description"))
            or _mapping_value(poll_info, ("question", "title", "content"))
            or "Bình chọn trong nhóm",
            "poll_id": _text(
                params.get("pollId")
                or params.get("poll_id")
                or params.get("id")
                or content.get("pollId")
                or content.get("poll_id")
                or poll_info.get("pollId")
                or poll_info.get("poll_id")
            ),
            "action": _text(content.get("action")),
        }
        return "poll", {key: value for key, value in media.items() if value is not None}

    if msg_type == "chat.recommended":
        contact_user_id = _mapping_value(
            params,
            ("contactUid", "contact_uid", "userId", "uid"),
        ) or _mapping_value(
            content,
            ("contactUid", "contact_uid", "userId", "uid"),
        )
        if contact_user_id:
            media = {
                "contact_user_id": contact_user_id,
                "contact_name": _mapping_value(
                    content,
                    ("title", "displayName", "name"),
                )
                or _mapping_value(
                    params,
                    ("displayName", "name", "title"),
                ),
                "contact_phone": _mapping_value(
                    params,
                    ("phone", "phoneNumber"),
                )
                or _mapping_value(content, ("phone", "phoneNumber")),
                "thumbnail_url": thumb
                or _safe_url(params.get("qrCodeUrl"))
                or _safe_url(content.get("qrCodeUrl")),
            }
            return "contact", {
                key: value for key, value in media.items() if value is not None
            }

    if msg_type in {"chat.recommended", "chat.link", "chat.location.new"}:
        media = {
            "url": href or _safe_url(params.get("redirect_url")),
            "thumbnail_url": thumb,
            "title": _mapping_value(content, ("title", "description"))
            or _mapping_value(params, ("mediaTitle", "title")),
            "description": _mapping_value(content, ("description",)),
        }
        return "link", {key: value for key, value in media.items() if value is not None}

    return "text", None


def message_text(payload: dict) -> str:
    kind, media = message_media(payload)
    message = payload.get("message")
    direct = _text(message)
    if direct:
        return direct
    from_message = _mapping_value(
        message,
        ("text", "content", "message", "title", "description"),
    )
    if from_message:
        return from_message
    message_object = payload.get("message_object")
    from_object = _mapping_value(
        message_object,
        ("content", "text", "message", "title", "description"),
    )
    if from_object:
        return from_object
    content = message_object.get("content") if isinstance(message_object, dict) else None
    nested = _mapping_value(content, ("text", "title", "description"))
    if nested:
        return nested
    if kind == "file" and media:
        return f"Tệp: {media.get('filename', 'Tệp đính kèm')}"
    if kind == "poll" and media:
        return f"Bình chọn: {media.get('question', 'Bình chọn trong nhóm')}"
    return MEDIA_LABELS.get(kind, "[Nội dung không hỗ trợ]")


def thread_type(payload: dict) -> str:
    value = str(payload.get("thread_type") or "user").lower()
    return "group" if "group" in value else "user"


def is_own_author(author_id: str | None, account_uid: str | None) -> bool:
    return bool(author_id and (author_id == "0" or author_id == account_uid))


def sender_name(payload: dict, account_uid: str | None) -> str:
    author_id = _text(payload.get("author_id"))
    if is_own_author(author_id, account_uid):
        return "Bạn"
    message_object = payload.get("message_object")
    name = _mapping_value(
        message_object,
        ("dName", "displayName", "senderName", "name", "userName"),
    )
    if name:
        return name
    author_id = _text(payload.get("author_id"))
    if author_id and author_id == account_uid:
        return "Bạn"
    return f"Thành viên {author_id[-6:]}" if author_id else "Thành viên Zalo"


def thread_name(payload: dict, thread_id: str, account_uid: str | None) -> str:
    message_object = payload.get("message_object")
    kind = thread_type(payload)
    candidates = (
        ("groupName", "group_name", "groupTitle")
        if kind == "group"
        else ("dName", "displayName", "senderName", "name", "userName")
    )
    author_id = _text(payload.get("author_id"))
    name = (
        None
        if kind == "user" and is_own_author(author_id, account_uid)
        else _mapping_value(message_object, candidates)
    )
    if name:
        return name
    author_id = _text(payload.get("author_id"))
    if kind == "user" and author_id and not is_own_author(author_id, account_uid):
        return f"Zalo {author_id[-6:]}"
    if kind == "group":
        return f"Nhóm Zalo • {thread_id[-6:]}"
    return f"Zalo {thread_id[-6:]}"


def initials(name: str) -> str:
    words = [word for word in name.replace("-", " ").split() if word]
    if not words:
        return "ZL"
    if len(words) == 1:
        return words[0][:2].upper()
    return f"{words[0][0]}{words[-1][0]}".upper()


def incoming_message(event: Event, account: ZaloAccount) -> dict:
    payload = event.payload or {}
    kind, media = message_media(payload)
    reference = message_reference(payload)
    message_id = reference["msg_id"] or reference["cli_msg_id"]
    author_id = _text(payload.get("author_id"))
    return {
        "id": message_id or f"event:{event.id}",
        "event_id": event.id,
        "job_id": None,
        "thread_id": event.thread_id,
        "thread_type": thread_type(payload),
        "author_id": author_id,
        "author_name": sender_name(payload, account.zalo_uid),
        "thread_name": thread_name(payload, event.thread_id or "", account.zalo_uid),
        "text": message_text(payload),
        "kind": kind,
        "media": media,
        "message_ref": reference,
        "quote": message_quote(payload),
        "reactions": [],
        "forwarded": bool(_message_object(payload).get("reference")),
        "undone": False,
        "mine": is_own_author(author_id, account.zalo_uid),
        "status": "received",
        "created_at": utc_isoformat(event.created_at),
    }


def outgoing_message(job: Job) -> dict:
    payload = job.payload or {}
    message = payload.get("message")
    context = payload.get("_chat_context")
    context = context if isinstance(context, dict) else {}
    media = context.get("media") if isinstance(context.get("media"), dict) else None
    message_kind = str(context.get("kind") or "text")
    media_labels = {
        "gif": "[GIF]",
        "contact": "[Danh thiáº¿p]",
        "image": "[Hình ảnh]",
        "video": "[Video]",
        "audio": "[Tin nhắn thoại]",
        "file": "[Tệp đính kèm]",
        "media": "[Nhiều nội dung đính kèm]",
        "sticker": "[Sticker]",
        "link": "[Liên kết]",
    }
    text = (
        _text(message)
        or _mapping_value(message, ("text", "content", "message"))
        or _text(payload.get("text"))
        or media_labels.get(message_kind)
        or "[Nội dung không phải văn bản]"
    )
    kind = "group" if "group" in str(payload.get("thread_type") or "").lower() else "user"
    thread_id = _text(payload.get("thread_id"))
    return {
        "id": f"job:{job.id}",
        "event_id": None,
        "job_id": job.id,
        "thread_id": thread_id,
        "thread_type": kind,
        "author_id": None,
        "author_name": "Bạn",
        "thread_name": (
            f"Nhóm Zalo • {(thread_id or '')[-6:]}"
            if kind == "group"
            else f"Zalo {(thread_id or '')[-6:]}"
        ),
        "text": text,
        "kind": message_kind,
        "media": media,
        "message_ref": None,
        "quote": context.get("reply") if isinstance(context.get("reply"), dict) else None,
        "reactions": [],
        "forwarded": bool(context.get("forwarded")),
        "undone": False,
        "mine": True,
        "status": job.status,
        "error": job.error,
        "created_at": utc_isoformat(job.created_at),
    }


def group_metadata(session: Session, account_id: str) -> dict[str, dict]:
    snapshot = session.scalar(
        select(Event)
        .where(
            Event.account_id == account_id,
            Event.event_type == "group.metadata.synced",
        )
        .order_by(desc(Event.id))
        .limit(1)
    )
    groups = (snapshot.payload or {}).get("groups") if snapshot else None
    if not isinstance(groups, dict):
        return {}
    return {
        str(group_id): metadata
        for group_id, metadata in groups.items()
        if isinstance(metadata, dict)
    }


def conversation_payloads(
    session: Session,
    account: ZaloAccount,
    *,
    user_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    groups = group_metadata(session, account.id)
    scan_limit = min(max(limit * 20, 500), 5000)
    events = session.scalars(
        select(Event)
        .where(
            Event.account_id == account.id,
            Event.event_type == "message.received",
            Event.thread_id.is_not(None),
        )
        .order_by(desc(Event.id))
        .limit(scan_limit)
    ).all()
    jobs = session.scalars(
        select(Job)
        .where(
            Job.account_id == account.id,
            Job.action.in_(("sendMessage", "replyMessage", "__chat_forward__", "__chat_media__", "__chat_rich_media__")),
        )
        .order_by(desc(Job.created_at))
        .limit(scan_limit)
    ).all()

    read_cursors: dict[str, int] = {}
    if user_id:
        read_cursors = {
            item.thread_id: item.last_read_event_id
            for item in session.scalars(
                select(ConversationRead).where(
                    ConversationRead.user_id == user_id,
                    ConversationRead.account_id == account.id,
                )
            ).all()
        }

    latest_event_ids: dict[str, int] = {}
    listener_ids_by_thread: dict[str, set[str]] = {}
    unread: dict[str, dict[str, int]] = {}
    counterpart_names: dict[str, str] = {}
    items: list[tuple[object, dict]] = []
    for event in events:
        thread_id = event.thread_id
        if thread_id:
            latest_event_ids[thread_id] = max(
                event.id,
                latest_event_ids.get(thread_id, 0),
            )
        if is_projectable_message(event.payload or {}):
            listener_ids_by_thread.setdefault(thread_id, set()).update(
                _event_message_ids(event)
            )
    for event in _deduplicated_projectable_events(events):
        thread_id = event.thread_id
        message = incoming_message(event, account)
        if message["thread_id"]:
            items.append((event.created_at, message))
            if (
                message["thread_type"] == "user"
                and not message["mine"]
            ):
                counterpart_names.setdefault(thread_id, message["author_name"])
            if user_id and not message["mine"] and event.id > read_cursors.get(thread_id, 0):
                summary = unread.setdefault(
                    thread_id,
                    {"count": 0, "first_event_id": event.id},
                )
                summary["count"] += 1
                summary["first_event_id"] = min(summary["first_event_id"], event.id)
    for job in jobs:
        message = outgoing_message(job)
        if message["thread_id"] and not _job_has_listener_echo(
            job,
            listener_ids_by_thread.get(message["thread_id"], set()),
        ):
            items.append((job.created_at, message))
    items.sort(key=lambda item: item[0], reverse=True)
    profile_cache = cached_profiles(session, account_id=account.id)

    conversations: dict[str, dict] = {}
    for _, message in items:
        thread_id = message["thread_id"]
        if thread_id in conversations:
            continue
        metadata = groups.get(thread_id, {})
        kind = "group" if metadata or message["thread_type"] == "group" else "user"
        profile = profile_cache.get(thread_id) if kind == "user" else None
        name = (
            _text(metadata.get("name"))
            if kind == "group"
            else _text(profile.name if profile else None)
            or counterpart_names.get(thread_id)
        )
        name = name or (
            f"Nhóm Zalo • {thread_id[-6:]}" if kind == "group" else f"Zalo {thread_id[-6:]}"
        )
        conversations[thread_id] = {
            "thread_id": thread_id,
            "thread_type": kind,
            "name": name,
            "initials": initials(name),
            "avatar_url": (
                _text(metadata.get("avatar_url"))
                if kind == "group"
                else _text(profile.avatar_url if profile else None)
            ),
            "profile_cached": bool(profile) if kind == "user" else True,
            "relationship_status": (
                profile.relationship_status if profile else "unknown"
            ) if kind == "user" else None,
            "blocked_by_me": bool(profile.blocked_by_me) if profile else False,
            "member_count": metadata.get("member_count") if kind == "group" else None,
            "preview": message["text"],
            "preview_author_name": message["author_name"],
            "last_message_at": message["created_at"],
            "last_message_mine": message["mine"],
            "status": message["status"],
            "latest_event_id": latest_event_ids.get(thread_id, 0),
            "unread_count": unread.get(thread_id, {}).get("count", 0),
            "first_unread_event_id": unread.get(thread_id, {}).get("first_event_id"),
        }
        if len(conversations) >= limit:
            break
    return list(conversations.values())


def message_payloads(
    session: Session,
    account: ZaloAccount,
    *,
    selected_thread_id: str,
    around_event_id: int | None = None,
    before_event_id: int | None = None,
    after_event_id: int | None = None,
    user_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    scan_limit = min(max(limit * 5, limit), 2500)
    event_filter = (
        Event.account_id == account.id,
        Event.event_type == "message.received",
        Event.thread_id == selected_thread_id,
    )
    if around_event_id is None:
        event_query = select(Event).where(*event_filter)
        if before_event_id is not None:
            event_query = event_query.where(Event.id < before_event_id)
        if after_event_id is not None:
            event_query = event_query.where(Event.id > after_event_id)
            event_order = Event.id
        else:
            event_order = desc(Event.id)
        events = session.scalars(event_query.order_by(event_order).limit(scan_limit)).all()
    else:
        before_target = session.scalars(
            select(Event)
            .where(*event_filter, Event.id <= around_event_id)
            .order_by(desc(Event.id))
            .limit(scan_limit)
        ).all()
        after_target = session.scalars(
            select(Event)
            .where(*event_filter, Event.id > around_event_id)
            .order_by(Event.id)
            .limit(scan_limit)
        ).all()
        before_projectable = [
            event
            for event in before_target
            if is_projectable_message(event.payload or {})
        ][: max(limit // 2, 1)]
        after_projectable = [
            event
            for event in after_target
            if is_projectable_message(event.payload or {})
        ][: max(limit - len(before_projectable), 0)]
        events = list(reversed(before_projectable)) + after_projectable

    jobs = []
    if around_event_id is None and before_event_id is None and after_event_id is None:
        jobs = session.scalars(
            select(Job)
            .where(
                Job.account_id == account.id,
                Job.action.in_(("sendMessage", "replyMessage", "__chat_forward__", "__chat_media__")),
            )
            .order_by(desc(Job.created_at))
            .limit(min(limit * 4, 1000))
        ).all()
    projectable_events = _deduplicated_projectable_events(events)
    messages = [
        incoming_message(event, account)
        for event in projectable_events
    ]
    author_ids = {
        str(message["author_id"])
        for message in messages
        if message.get("author_id")
    }
    profile_cache = cached_profiles(
        session,
        account_id=account.id,
        user_ids=author_ids,
    )
    for message in messages:
        profile = profile_cache.get(str(message.get("author_id") or ""))
        message["author_avatar_url"] = (
            _text(profile.avatar_url) if profile else None
        )
        if profile and not message["mine"]:
            message["author_name"] = profile.name
    listener_message_ids: set[str] = set()
    for event in events:
        if is_projectable_message(event.payload or {}):
            listener_message_ids.update(_event_message_ids(event))
    since_event_id = min((event.id for event in projectable_events), default=0)
    reaction_map = _reaction_summaries(
        session,
        account,
        selected_thread_id,
        user_id=user_id,
        since_event_id=since_event_id,
    )
    undone_ids = _undone_message_ids(
        session,
        account.id,
        selected_thread_id,
        since_event_id=since_event_id,
    )
    for message in messages:
        reference = message.get("message_ref") or {}
        identity_values = {
            str(value)
            for value in (
                message.get("id"),
                reference.get("msg_id"),
                reference.get("cli_msg_id"),
            )
            if value not in (None, "")
        }
        if identity_values & undone_ids:
            message.update(
                text="[Tin nhắn đã được thu hồi]",
                kind="undo",
                media=None,
                quote=None,
                reactions=[],
                undone=True,
            )
        else:
            for identity in identity_values:
                if identity in reaction_map:
                    message["reactions"] = reaction_map[identity]
                    break
    messages.extend(
        outgoing_message(job)
        for job in jobs
        if _text((job.payload or {}).get("thread_id")) == selected_thread_id
        and not _job_has_listener_echo(job, listener_message_ids)
    )
    messages.sort(
        key=lambda item: (
            item["created_at"],
            int(item.get("event_id") or 0),
            item.get("id") or "",
        )
    )
    if around_event_id is not None:
        return messages
    if after_event_id is not None:
        return messages[:limit]
    return messages[-limit:]


def _reaction_summaries(
    session: Session,
    account: ZaloAccount,
    thread_id: str,
    *,
    user_id: str | None,
    since_event_id: int,
) -> dict[str, list[dict]]:
    if since_event_id <= 0:
        return {}
    rows = session.scalars(
        select(Event)
        .where(
            Event.account_id == account.id,
            Event.thread_id == thread_id,
            Event.id >= since_event_id,
            or_(
                Event.event_type == "chat.reaction.local",
                Event.event_type == "message.received",
            ),
        )
        .order_by(Event.id)
    ).all()
    catalog_items = reaction_catalog(session, include_disabled=True)
    catalog_by_id = {item["id"]: item for item in catalog_items}
    catalog_by_icon = {item["zalo_icon"]: item for item in catalog_items}

    def presentation_for(
        *,
        reaction_id: str | None = None,
        zalo_icon: str | None = None,
        glyph: str | None = None,
        label: str | None = None,
        mode: str | None = None,
    ) -> dict[str, str]:
        item = catalog_by_id.get(reaction_id or "") or catalog_by_icon.get(zalo_icon or "")
        if item:
            return {
                "reaction_id": item["id"],
                "glyph": item["glyph"],
                "label": item["label"],
                "mode": item["mode"],
            }
        return reaction_presentation(
            session,
            catalog=catalog_items,
            reaction_id=reaction_id,
            zalo_icon=zalo_icon,
            glyph=glyph,
            label=label,
            mode=mode,
        )

    actor_state: dict[tuple[str, str], dict] = {}
    for event in rows:
        payload = event.payload or {}
        if event.event_type == "chat.reaction.local":
            target_id = _text(payload.get("target_message_id"))
            actor_id = _text(payload.get("actor_id")) or "system"
            if not target_id:
                continue
            actor_key = f"local:{actor_id}"
            if payload.get("action") == "remove":
                actor_state.pop((target_id, actor_key), None)
                continue
            presentation = presentation_for(
                reaction_id=_text(payload.get("reaction_id")),
                glyph=_text(payload.get("glyph")),
                label=_text(payload.get("label")),
                mode="local",
            )
            actor_state[(target_id, actor_key)] = {
                **presentation,
                "mine": bool(user_id and actor_id == user_id),
            }
            continue
        if message_type(payload) != "chat.reaction":
            continue
        content = payload.get("message")
        content = content if isinstance(content, dict) else {}
        targets = content.get("rMsg")
        if not isinstance(targets, list):
            continue
        icon = _text(content.get("rIcon"))
        message_object = _message_object(payload)
        actor_id = (
            _text(content.get("msgSender"))
            or _text(payload.get("author_id"))
            or _text(message_object.get("uidFrom"))
            or "unknown"
        )
        mine = actor_id == "0" or bool(account.zalo_uid and actor_id == account.zalo_uid)
        actor_key = f"zalo:{account.zalo_uid if actor_id == '0' else actor_id}"
        for target in targets:
            target_id = _mapping_value(target, ("gMsgID", "msgId", "cMsgID"))
            if not target_id:
                continue
            if not icon:
                actor_state.pop((target_id, actor_key), None)
                continue
            presentation = presentation_for(zalo_icon=icon)
            actor_state[(target_id, actor_key)] = {
                **presentation,
                "mine": mine,
            }
    aggregated: dict[str, dict[str, dict]] = {}
    for (target_id, _), reaction in actor_state.items():
        target = aggregated.setdefault(target_id, {})
        item = target.setdefault(
            reaction["reaction_id"],
            {
                "reaction_id": reaction["reaction_id"],
                "glyph": reaction["glyph"],
                "label": reaction["label"],
                "mode": reaction["mode"],
                "count": 0,
                "mine": False,
            },
        )
        item["count"] += 1
        item["mine"] = item["mine"] or reaction["mine"]
    return {
        target_id: sorted(
            values.values(),
            key=lambda item: (not item["mine"], item["reaction_id"]),
        )
        for target_id, values in aggregated.items()
    }


def _undone_message_ids(
    session: Session,
    account_id: str,
    thread_id: str,
    *,
    since_event_id: int,
) -> set[str]:
    if since_event_id <= 0:
        return set()
    rows = session.scalars(
        select(Event)
        .where(
            Event.account_id == account_id,
            Event.thread_id == thread_id,
            Event.id >= since_event_id,
            Event.event_type == "message.received",
        )
        .order_by(Event.id)
    ).all()
    result: set[str] = set()
    for event in rows:
        payload = event.payload or {}
        if message_type(payload) != "chat.undo":
            continue
        content = payload.get("message")
        content = content if isinstance(content, dict) else {}
        for key in ("globalMsgId", "msgId", "cliMsgId"):
            value = _text(content.get(key))
            if value:
                result.add(value)
    return result
