from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
import zstandard
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import (
    get_db,
    get_principal,
    require_scope,
    require_short_lived_scope,
    verify_csrf,
    write_audit,
)
from .catalog import catalog_payload, get_action
from .chat import (
    conversation_payloads,
    group_metadata,
    incoming_message,
    is_projectable_message,
    message_media,
    message_payloads,
    message_reference,
    message_text,
    outgoing_message,
)
from .media import (
    gif_library_media_url,
    infer_media_kind,
    outbound_media_url,
    safe_upload_name,
    validate_media_kind,
    validate_upload_name,
)
from .models import (
    ApiKey,
    AppSetting,
    Archive,
    AuditLog,
    ConversationRead,
    Event,
    GifLibraryItem,
    Job,
    MediaUpload,
    User,
    ZaloAccount,
    ZaloContactProfile,
    utcnow,
)
from .profiles import (
    backfill_friend_profiles,
    cached_profiles,
    normalize_profiles,
    profile_initials,
    profile_records,
)
from .queue import cancel_job, enqueue_job, retry_job
from .reactions import (
    reaction_by_id,
    reaction_catalog,
    save_reaction_catalog,
)
from .schemas import (
    AccountCreate,
    AccountDelete,
    AccountUpdate,
    ActionInvoke,
    ApiKeyCreate,
    ChatForwardCreate,
    ChatFriendActionCreate,
    ChatGifImportCreate,
    ChatGifLibraryDelete,
    ChatGifLibraryUpdate,
    ChatGroupInviteCreate,
    ChatGroupRenameCreate,
    ChatMediaCreate,
    ChatMessageCreate,
    ChatProfileSyncCreate,
    ChatReactionCreate,
    ChatReadStateUpdate,
    ChatRichMediaCreate,
    ChatStickerPreferencesUpdate,
    ChatUndoCreate,
    PasswordChange,
    ReactionCatalogUpdate,
    SettingUpdate,
    UserCreate,
    UserUpdate,
)
from .security import (
    ROLE_SCOPES,
    Principal,
    generate_api_key,
    hash_secret,
    is_expired,
    safe_child_path,
    verify_secret,
)

router = APIRouter(prefix="/api/v1")

_GIPHY_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{6,64}$")
_GIPHY_MEDIA_HOST_PATTERN = re.compile(r"^media[0-9]*\.giphy\.com$")


def _require_chat_account(session: Session, account_id: str) -> ZaloAccount:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if not account.enabled:
        raise HTTPException(status_code=409, detail="Zalo account is disabled")
    if account.status == "error":
        raise HTTPException(
            status_code=409,
            detail="Phiên Zalo đang lỗi; admin phải cập nhật cookie trước khi gửi.",
        )
    if not account.listener_enabled or account.status != "online":
        raise HTTPException(
            status_code=409,
            detail="Listener của tài khoản phải online trước khi gửi tin.",
        )
    return account


def _chat_source_event(
    session: Session,
    *,
    account_id: str,
    event_id: int,
) -> Event:
    event = session.get(Event, event_id)
    if (
        event is None
        or event.account_id != account_id
        or event.event_type != "message.received"
        or not event.thread_id
        or not is_projectable_message(event.payload or {})
    ):
        raise HTTPException(status_code=404, detail="Không tìm thấy tin nhắn nguồn")
    return event


def account_payload(account: ZaloAccount) -> dict:
    return {
        "id": account.id,
        "name": account.name,
        "zalo_uid": account.zalo_uid,
        "enabled": account.enabled,
        "listener_enabled": account.listener_enabled,
        "status": account.status,
        "last_seen_at": account.last_seen_at,
        "last_error": account.last_error,
        "circuit_failures": account.circuit_failures,
        "circuit_open_until": account.circuit_open_until,
        "has_credentials": bool(account.cookie_cipher and account.imei_cipher),
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


def job_payload(job: Job) -> dict:
    return {
        "id": job.id,
        "account_id": job.account_id,
        "account_name": job.account.name if job.account else None,
        "action": job.action,
        "payload": job.payload,
        "status": job.status,
        "priority": job.priority,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "active": user.active,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
    }


def _contact_text(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _contact_records(value: Any) -> list[tuple[str | None, dict]]:
    return profile_records(value)


def _normalize_contacts(result: Any) -> list[dict]:
    return normalize_profiles(result)


def _contact_profile_payload(profile: ZaloContactProfile | None, user_id: str) -> dict:
    name = profile.name if profile else f"Zalo {user_id[-6:]}"
    return {
        "user_id": user_id,
        "name": name,
        "initials": profile_initials(name),
        "avatar_url": profile.avatar_url if profile else None,
        "phone": profile.phone if profile else None,
        "online": profile.online if profile else None,
        "relationship_status": (
            profile.relationship_status if profile else "unknown"
        ),
        "blocked_by_me": bool(profile.blocked_by_me) if profile else False,
        "synced_at": profile.synced_at if profile else None,
    }


def _contacts_job_payload(job: Job | None) -> dict | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "status": job.status,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def _sticker_setting_key(principal: Principal, account_id: str) -> str:
    return f"chat.stickers.{principal.kind}.{principal.id}.{account_id}"


def _sticker_snapshot(value: Any, *, last_used_at: Any = None) -> dict | None:
    if not isinstance(value, dict):
        return None
    sticker_id = _contact_text(value.get("sticker_id") or value.get("stickerId"))
    category_id = _contact_text(
        value.get("category_id")
        or value.get("categoryId")
        or value.get("cate_id")
        or value.get("cateId")
    )
    if (
        not sticker_id
        or not category_id
        or not sticker_id.isdigit()
        or not category_id.isdigit()
        or int(sticker_id) <= 0
        or int(category_id) <= 0
    ):
        return None

    def bounded_int(candidate: Any, fallback: int, upper: int) -> int:
        try:
            parsed = int(candidate)
        except (TypeError, ValueError):
            return fallback
        return parsed if 0 <= parsed <= upper else fallback

    thumbnail_url = _contact_text(
        value.get("thumbnail_url")
        or value.get("thumbnailUrl")
        or value.get("url")
    )
    if thumbnail_url:
        parsed = urlsplit(thumbnail_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            thumbnail_url = None
    thumbnail_url = thumbnail_url or (
        "https://zalo-api.zadn.vn/api/emoticon/sticker/"
        f"webpc?eid={sticker_id}&size=130"
    )
    used_at = (
        last_used_at.isoformat()
        if hasattr(last_used_at, "isoformat")
        else _contact_text(last_used_at)
    )
    return {
        "key": f"{category_id}:{sticker_id}",
        "sticker_id": sticker_id,
        "category_id": category_id,
        "sticker_type": bounded_int(
            value.get("sticker_type") or value.get("stickerType"),
            1,
            100,
        ),
        "thumbnail_url": thumbnail_url,
        "width": bounded_int(value.get("width"), 130, 2000) or 130,
        "height": bounded_int(value.get("height"), 130, 2000) or 130,
        "last_used_at": used_at,
    }


def _chat_sticker_catalog(
    session: Session,
    *,
    account_id: str,
    principal: Principal,
) -> dict:
    setting_key = _sticker_setting_key(principal, account_id)
    setting = session.get(AppSetting, setting_key)
    setting_value = setting.value if setting and isinstance(setting.value, dict) else {}
    stored_favorites = setting_value.get("favorites")
    stored_favorites = stored_favorites if isinstance(stored_favorites, list) else []

    favorites: list[dict] = []
    favorite_keys: set[str] = set()
    for value in stored_favorites:
        sticker = _sticker_snapshot(value)
        if sticker is None or sticker["key"] in favorite_keys:
            continue
        sticker["favorite"] = True
        favorite_keys.add(sticker["key"])
        favorites.append(sticker)

    candidates: list[tuple[Any, dict]] = []
    events = session.scalars(
        select(Event)
        .where(
            Event.account_id == account_id,
            Event.event_type == "message.received",
        )
        .order_by(desc(Event.id))
        .limit(1500)
    ).all()
    for event in events:
        kind, media = message_media(event.payload or {})
        if kind != "sticker" or not isinstance(media, dict):
            continue
        sticker = _sticker_snapshot(media, last_used_at=event.created_at)
        if sticker is not None:
            candidates.append((event.created_at, sticker))

    jobs = session.scalars(
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.action == "__chat_rich_media__",
            Job.status.in_(("queued", "running", "succeeded")),
        )
        .order_by(desc(Job.created_at))
        .limit(500)
    ).all()
    for job in jobs:
        payload = job.payload or {}
        context = payload.get("_chat_context")
        context = context if isinstance(context, dict) else {}
        media = context.get("media") or payload.get("media")
        if str(context.get("kind") or payload.get("kind") or "") != "sticker":
            continue
        sticker = _sticker_snapshot(media, last_used_at=job.created_at)
        if sticker is not None:
            candidates.append((job.created_at, sticker))

    candidates.sort(key=lambda item: item[0], reverse=True)
    recent: list[dict] = []
    recent_keys: set[str] = set()
    for _, sticker in candidates:
        if sticker["key"] in recent_keys:
            continue
        sticker["favorite"] = sticker["key"] in favorite_keys
        recent.append(sticker)
        recent_keys.add(sticker["key"])
        if len(recent) >= 48:
            break

    items = [*favorites]
    item_keys = set(favorite_keys)
    for sticker in recent:
        if sticker["key"] in item_keys:
            favorite = next(
                (item for item in items if item["key"] == sticker["key"]),
                None,
            )
            if favorite is not None and sticker.get("last_used_at"):
                favorite["last_used_at"] = sticker["last_used_at"]
            continue
        items.append(sticker)
        item_keys.add(sticker["key"])

    return {
        "items": items[:120],
        "favorites": favorites,
        "recent": recent,
        "favorite_count": len(favorites),
        "recent_count": len(recent),
        "source": "Lịch sử Listener và sticker đã gửi từ ZChat",
    }


def _gif_library_item_payload(item: GifLibraryItem, settings: Any) -> dict:
    return {
        "id": item.id,
        "name": item.original_name,
        "size_bytes": item.size_bytes,
        "content_type": item.content_type,
        "width": item.width,
        "height": item.height,
        "sha256": item.sha256,
        "favorite": item.favorite,
        "use_count": item.use_count,
        "created_by_id": item.created_by_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "last_used_at": item.last_used_at,
        "preview_url": gif_library_media_url(settings, item.id),
    }


def _gif_library_usage(session: Session) -> tuple[int, int]:
    count, size = session.execute(
        select(
            func.count(GifLibraryItem.id),
            func.coalesce(func.sum(GifLibraryItem.size_bytes), 0),
        )
    ).one()
    return int(count or 0), int(size or 0)


def _gif_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _giphy_gif_id(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").rstrip(".").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Link Giphy không hợp lệ") from exc
    if parsed.scheme.lower() != "https" or port not in {None, 443}:
        raise HTTPException(
            status_code=422,
            detail="Link Giphy phải dùng HTTPS",
        )

    parts = [part for part in parsed.path.split("/") if part]
    candidate = ""
    if host in {"giphy.com", "www.giphy.com"}:
        if len(parts) >= 2 and parts[0].lower() == "embed":
            candidate = parts[1]
        elif len(parts) >= 2 and parts[0].lower() in {"gifs", "stickers"}:
            candidate = parts[-1].rsplit("-", 1)[-1]
    elif _GIPHY_MEDIA_HOST_PATTERN.fullmatch(host):
        if len(parts) >= 2 and parts[0].lower() == "media":
            candidate = parts[1]

    if not _GIPHY_ID_PATTERN.fullmatch(candidate):
        raise HTTPException(
            status_code=422,
            detail="Không nhận dạng được GIF ID từ link giphy.com",
        )
    return candidate


def _gif_dimensions(path: Path) -> tuple[int | None, int | None]:
    with path.open("rb") as stream:
        header = stream.read(10)
    if len(header) < 10 or header[:6] not in {b"GIF87a", b"GIF89a"}:
        return None, None
    width = int.from_bytes(header[6:8], "little")
    height = int.from_bytes(header[8:10], "little")
    return (width or None), (height or None)


def _download_giphy_gif(
    *,
    gif_id: str,
    target: Path,
    max_bytes: int,
    timeout_seconds: int,
) -> int:
    media_url = f"https://media.giphy.com/media/{gif_id}/giphy.gif"
    try:
        response = requests.get(
            media_url,
            stream=True,
            allow_redirects=False,
            timeout=(5, min(timeout_seconds, 30)),
            headers={"User-Agent": "ZLAPI-Control-Center/1.0"},
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail="Không thể kết nối tới Giphy; hãy thử lại sau",
        ) from exc

    try:
        if response.status_code != 200:
            raise HTTPException(
                status_code=422,
                detail="Giphy không trả về GIF hợp lệ từ link này",
            )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type != "image/gif":
            raise HTTPException(
                status_code=422,
                detail="Nội dung từ Giphy không phải tệp GIF",
            )
        try:
            content_length = int(response.headers.get("content-length", "0") or 0)
        except ValueError:
            content_length = 0
        if content_length > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"GIF vượt giới hạn {max_bytes // (1024 * 1024)} MB",
            )

        size = 0
        with target.open("xb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"GIF vượt giới hạn {max_bytes // (1024 * 1024)} MB",
                    )
                output.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="Giphy trả về tệp GIF rỗng")
        return size
    finally:
        response.close()


def _persist_gif_upload(
    session: Session,
    *,
    upload: MediaUpload,
    settings: Any,
    created_by_id: str | None,
    width: int | None,
    height: int | None,
    mark_used: bool = True,
) -> tuple[GifLibraryItem, bool]:
    source = Path(upload.stored_path)
    if not safe_child_path(settings.upload_dir, source) or not source.is_file():
        raise HTTPException(status_code=422, detail="Tệp GIF tạm không còn tồn tại")
    with source.open("rb") as stream:
        signature = stream.read(6)
    if signature not in {b"GIF87a", b"GIF89a"}:
        raise HTTPException(status_code=422, detail="Tệp không có chữ ký GIF hợp lệ")
    digest = _gif_sha256(source)
    existing = session.scalar(
        select(GifLibraryItem).where(GifLibraryItem.sha256 == digest)
    )
    if existing is not None:
        if existing.width is None and width:
            existing.width = width
        if existing.height is None and height:
            existing.height = height
        if mark_used:
            existing.last_used_at = utcnow()
            existing.use_count += 1
        return existing, False

    used_items, used_bytes = _gif_library_usage(session)
    if used_items >= settings.gif_library_max_items:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Thư viện đã đạt giới hạn {settings.gif_library_max_items} GIF. "
                "Hãy xóa GIF cũ hoặc bỏ chọn lưu vào thư viện."
            ),
        )
    if used_bytes + upload.size_bytes > settings.gif_library_max_bytes:
        raise HTTPException(
            status_code=409,
            detail="Thư viện GIF đã hết dung lượng. Hãy xóa GIF cũ hoặc bỏ chọn lưu.",
        )

    settings.gif_library_dir.mkdir(parents=True, exist_ok=True)
    target = settings.gif_library_dir / f"{digest}.gif"
    if not target.exists():
        shutil.copy2(source, target)
    item = GifLibraryItem(
        sha256=digest,
        original_name=safe_upload_name(upload.original_name),
        stored_path=str(target),
        size_bytes=upload.size_bytes,
        content_type="image/gif",
        width=width,
        height=height,
        favorite=False,
        use_count=1 if mark_used else 0,
        created_by_id=created_by_id,
        last_used_at=utcnow() if mark_used else None,
    )
    session.add(item)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(GifLibraryItem).where(GifLibraryItem.sha256 == digest)
        )
        if existing is None:
            raise
        if mark_used:
            existing.last_used_at = utcnow()
            existing.use_count += 1
        return existing, False
    return item, True


def _group_info_job_payload(job: Job | None) -> dict | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "status": job.status,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def _group_info_jobs(
    session: Session,
    account_id: str,
    thread_id: str,
    *,
    status_filter: str | None = None,
) -> list[Job]:
    statement = (
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.action == "__chat_group_info__",
        )
        .order_by(desc(Job.created_at))
        .limit(500)
    )
    if status_filter:
        statement = statement.where(Job.status == status_filter)
    return [
        job
        for job in session.scalars(statement).all()
        if str((job.payload or {}).get("thread_id") or "") == str(thread_id)
    ]


def _group_pin_jobs(
    session: Session,
    account_id: str,
    thread_id: str,
) -> list[Job]:
    jobs = session.scalars(
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.action == "__chat_group_pins__",
        )
        .order_by(desc(Job.created_at))
        .limit(500)
    ).all()
    return [
        job
        for job in jobs
        if str((job.payload or {}).get("thread_id") or "") == str(thread_id)
    ]


def _group_note_jobs(
    session: Session,
    account_id: str,
    thread_id: str,
) -> list[Job]:
    jobs = session.scalars(
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.action == "__chat_group_notes__",
        )
        .order_by(desc(Job.created_at))
        .limit(500)
    ).all()
    return [
        job
        for job in jobs
        if str((job.payload or {}).get("thread_id") or "") == str(thread_id)
    ]


def _group_poll_jobs(
    session: Session,
    account_id: str,
    thread_id: str,
) -> list[Job]:
    jobs = session.scalars(
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.action == "__chat_group_polls__",
        )
        .order_by(desc(Job.created_at))
        .limit(500)
    ).all()
    return [
        job
        for job in jobs
        if str((job.payload or {}).get("thread_id") or "") == str(thread_id)
    ]


def _group_poll_detail_jobs(
    session: Session,
    account_id: str,
    thread_id: str,
    poll_id: str,
) -> list[Job]:
    jobs = session.scalars(
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.action == "__chat_poll_detail__",
        )
        .order_by(desc(Job.created_at))
        .limit(500)
    ).all()
    return [
        job
        for job in jobs
        if str((job.payload or {}).get("thread_id") or "") == str(thread_id)
        and str((job.payload or {}).get("poll_id") or "") == str(poll_id)
    ]


def _group_notes_snapshot(result: Any) -> list[dict]:
    result = result if isinstance(result, dict) else {}
    raw_notes = result.get("notes")
    if not isinstance(raw_notes, list):
        raw_notes = []
    notes: list[dict] = []
    seen: set[str] = set()
    for item in raw_notes[:100]:
        if not isinstance(item, dict):
            continue
        note_id = _contact_text(item.get("note_id") or item.get("id"))
        content = _contact_text(
            item.get("content") or item.get("title") or item.get("text")
        )
        if not note_id or not content or note_id in seen:
            continue
        seen.add(note_id)
        created_at = item.get("created_at")
        updated_at = item.get("updated_at")
        if not isinstance(created_at, (str, int, float)) or isinstance(created_at, bool):
            created_at = None
        if not isinstance(updated_at, (str, int, float)) or isinstance(updated_at, bool):
            updated_at = None
        notes.append(
            {
                "note_id": note_id,
                "content": content[:4000],
                "creator_id": _contact_text(item.get("creator_id")),
                "creator_name": _contact_text(item.get("creator_name")),
                "editor_id": _contact_text(item.get("editor_id")),
                "editor_name": _contact_text(item.get("editor_name")),
                "created_at": created_at,
                "updated_at": updated_at,
                "emoji": (_contact_text(item.get("emoji")) or "📝")[:8],
                "color": _contact_text(item.get("color")),
            }
        )
    return notes


def _group_polls_snapshot(result: Any) -> list[dict]:
    result = result if isinstance(result, dict) else {}
    raw_polls = result.get("polls")
    if not isinstance(raw_polls, list):
        raw_polls = []

    def as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "ended", "closed", "locked"}

    def as_count(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    polls: list[dict] = []
    seen: set[str] = set()
    for item in raw_polls[:100]:
        if not isinstance(item, dict):
            continue
        poll_id = _contact_text(item.get("poll_id") or item.get("pollId") or item.get("id"))
        question = _contact_text(item.get("question") or item.get("title") or item.get("content"))
        if not poll_id or not question or poll_id in seen:
            continue
        seen.add(poll_id)
        raw_options = item.get("options")
        if not isinstance(raw_options, list):
            raw_options = []
        options: list[dict] = []
        for index, option in enumerate(raw_options[:100]):
            if not isinstance(option, dict):
                continue
            label = _contact_text(
                option.get("label")
                or option.get("content")
                or option.get("text")
                or option.get("title")
            )
            if not label:
                continue
            options.append(
                {
                    "option_id": _contact_text(option.get("option_id") or option.get("id"))
                    or f"option-{index + 1}",
                    "label": label[:500],
                    "vote_count": as_count(option.get("vote_count") or option.get("votes")),
                    "voted": as_bool(option.get("voted")),
                }
            )
        total_votes = as_count(item.get("total_votes") or item.get("num_vote"))
        if not total_votes:
            total_votes = sum(option["vote_count"] for option in options)
        status_value = _contact_text(item.get("status")) or "active"
        closed = as_bool(item.get("closed")) or status_value.lower() in {"ended", "closed", "locked", "expired"}
        polls.append(
            {
                "poll_id": poll_id,
                "question": question[:1000],
                "options": options,
                "total_votes": total_votes,
                "status": "ended" if closed else status_value[:40],
                "joined": as_bool(item.get("joined")),
                "allow_multiple": as_bool(item.get("allow_multiple") or item.get("allow_multi_choices")),
                "allow_add_new_option": as_bool(item.get("allow_add_new_option")),
                "anonymous": as_bool(item.get("anonymous") or item.get("is_anonymous")),
                "results_hidden": as_bool(item.get("results_hidden") or item.get("is_hide_vote_preview")),
                "created_at": item.get("created_at") if isinstance(item.get("created_at"), (str, int, float)) else None,
                "updated_at": item.get("updated_at") if isinstance(item.get("updated_at"), (str, int, float)) else None,
                "expired_at": item.get("expired_at") if isinstance(item.get("expired_at"), (str, int, float)) else None,
                "creator_id": _contact_text(item.get("creator_id")),
                "creator_name": _contact_text(item.get("creator_name")),
            }
        )
    return polls


def _group_poll_detail_snapshot(result: Any, poll_id: str) -> dict | None:
    result = result if isinstance(result, dict) else {}
    poll = result.get("poll")
    if isinstance(poll, dict):
        normalized = _group_polls_snapshot({"polls": [poll]})
        if normalized:
            return normalized[0]
    normalized = _group_polls_snapshot(result)
    if normalized:
        return next(
            (item for item in normalized if item["poll_id"] == str(poll_id)),
            normalized[0],
        )
    return None


def _group_pins_snapshot(
    session: Session,
    result: Any,
    *,
    account_id: str,
    thread_id: str,
) -> list[dict]:
    result = result if isinstance(result, dict) else {}
    raw_pins = result.get("pins")
    if not isinstance(raw_pins, list):
        raw_pins = []

    message_events = session.scalars(
        select(Event)
        .where(
            Event.account_id == account_id,
            Event.thread_id == thread_id,
            Event.event_type == "message.received",
        )
        .order_by(desc(Event.id))
        .limit(5000)
    ).all()
    event_by_message_id: dict[str, int] = {}
    for event in message_events:
        payload = event.payload or {}
        reference = message_reference(payload)
        message_object = payload.get("message_object")
        message_object = message_object if isinstance(message_object, dict) else {}
        for value in (
            reference.get("msg_id"),
            reference.get("cli_msg_id"),
            payload.get("message_id"),
            message_object.get("msgId"),
            message_object.get("globalMsgId"),
            message_object.get("cliMsgId"),
            message_object.get("clientMsgId"),
        ):
            identity = _contact_text(value)
            if identity and identity not in event_by_message_id:
                event_by_message_id[identity] = event.id

    pins: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_pins[:100]):
        if not isinstance(item, dict):
            continue
        message_id = _contact_text(
            item.get("message_id") or item.get("global_msg_id")
        )
        cli_message_id = _contact_text(
            item.get("cli_message_id") or item.get("client_msg_id")
        )
        pin_id = _contact_text(item.get("pin_id") or item.get("id"))
        identity = pin_id or message_id or cli_message_id or f"pin-{index}"
        if identity in seen:
            continue
        seen.add(identity)
        thumbnail = _contact_text(item.get("thumbnail_url") or item.get("thumb"))
        if thumbnail and not thumbnail.startswith(("https://", "http://")):
            thumbnail = None
        title = (
            _contact_text(item.get("title") or item.get("content"))
            or "Tin nhắn đã ghim"
        )
        event_id = next(
            (
                event_by_message_id[value]
                for value in (message_id, cli_message_id)
                if value and value in event_by_message_id
            ),
            None,
        )
        pinned_at = item.get("pinned_at")
        if not isinstance(pinned_at, (str, int, float)) or isinstance(pinned_at, bool):
            pinned_at = None
        pins.append(
            {
                "pin_id": pin_id,
                "message_id": message_id,
                "cli_message_id": cli_message_id,
                "event_id": event_id,
                "author_id": _contact_text(item.get("author_id")),
                "author_name": _contact_text(item.get("author_name")),
                "title": title[:500],
                "message_type": _contact_text(item.get("message_type")),
                "thumbnail_url": thumbnail,
                "pinned_at": pinned_at,
                "emoji": (_contact_text(item.get("emoji")) or "📌")[:8],
            }
        )
    return pins


def _active_group_management_job(
    session: Session,
    account_id: str,
    thread_id: str,
) -> Job | None:
    jobs = session.scalars(
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.action.in_(("changeGroupName", "addUsersToGroup")),
            Job.status.in_(("queued", "running")),
        )
        .order_by(desc(Job.created_at))
        .limit(200)
    ).all()
    return next(
        (
            job
            for job in jobs
            if str(
                (job.payload or {}).get("groupId")
                or (job.payload or {}).get("thread_id")
                or ""
            )
            == str(thread_id)
        ),
        None,
    )


def _group_info_snapshot(
    result: Any,
    *,
    thread_id: str,
    fallback: dict | None = None,
) -> dict:
    result = result if isinstance(result, dict) else {}
    raw_group = result.get("group")
    if not isinstance(raw_group, dict):
        raw_group = result.get("group_info")
    raw_group = raw_group if isinstance(raw_group, dict) else {}
    fallback = fallback if isinstance(fallback, dict) else {}

    def safe_avatar(value: Any) -> str | None:
        candidate = _contact_text(value)
        return (
            candidate
            if candidate and candidate.startswith(("https://", "http://"))
            else None
        )

    group_id = (
        _contact_text(
            raw_group.get("thread_id")
            or raw_group.get("groupId")
            or raw_group.get("id")
        )
        or thread_id
    )
    name = (
        _contact_text(raw_group.get("name") or raw_group.get("group_name"))
        or _contact_text(fallback.get("name"))
        or f"Nhóm Zalo • {thread_id[-6:]}"
    )
    members: list[dict] = []
    raw_members = result.get("members")
    if not isinstance(raw_members, list):
        raw_members = result.get("profiles")
    if isinstance(raw_members, dict):
        raw_members = [
            {**item, "user_id": item.get("user_id") or key}
            for key, item in raw_members.items()
            if isinstance(item, dict)
        ]
    if not isinstance(raw_members, list):
        raw_members = []
    seen: set[str] = set()
    owner_id = _contact_text(raw_group.get("owner_id") or raw_group.get("creatorId"))
    admin_ids = {
        str(value)
        for value in (
            raw_group.get("admin_ids")
            or raw_group.get("adminIds")
            or []
        )
        if _contact_text(value)
    }
    for item in raw_members:
        if not isinstance(item, dict):
            continue
        user_id = _contact_text(
            item.get("user_id")
            or item.get("userId")
            or item.get("id")
            or item.get("uid")
        )
        if not user_id or user_id in seen:
            continue
        member_name = (
            _contact_text(
                item.get("name")
                or item.get("displayName")
                or item.get("dName")
                or item.get("zaloName")
            )
            or f"Zalo {user_id[-6:]}"
        )
        role = _contact_text(item.get("role")) or (
            "owner"
            if user_id == owner_id
            else "admin"
            if user_id in admin_ids
            else "member"
        )
        if role not in {"owner", "admin", "member"}:
            role = "member"
        online = item.get("online")
        if online is None:
            online = item.get("isOnline")
        members.append(
            {
                "user_id": user_id,
                "name": member_name,
                "initials": "".join(
                    part[0] for part in member_name.replace("-", " ").split() if part
                )[:2].upper()
                or "ZL",
                "avatar_url": safe_avatar(
                    item.get("avatar_url")
                    or item.get("avatar")
                    or item.get("avatarUrl")
                    or item.get("avt")
                    or item.get("fullAvt")
                ),
                "online": bool(online) if online is not None else None,
                "role": role,
            }
        )
        seen.add(user_id)
    role_order = {"owner": 0, "admin": 1, "member": 2}
    members.sort(key=lambda item: (role_order[item["role"]], item["name"].casefold()))
    member_count = raw_group.get("member_count") or raw_group.get("totalMember")
    if not isinstance(member_count, int):
        member_count = len(members) or fallback.get("member_count")
    return {
        "group": {
            "thread_id": group_id,
            "name": name,
            "description": _contact_text(raw_group.get("description") or raw_group.get("desc")),
            "avatar_url": safe_avatar(
                raw_group.get("avatar_url")
                or raw_group.get("avatar")
                or raw_group.get("fullAvt")
                or raw_group.get("avt")
                or fallback.get("avatar_url")
            ),
            "member_count": member_count,
            "owner_id": owner_id,
            "admin_ids": sorted(admin_ids),
            "has_more_members": bool(
                raw_group.get("has_more_members") or raw_group.get("hasMoreMember")
            ),
        },
        "members": members,
    }


@router.get("/catalog")
def get_catalog(_: Principal = Depends(require_scope("read"))) -> list[dict]:
    return catalog_payload()


@router.get("/accounts")
def list_accounts(
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> list[dict]:
    accounts = session.scalars(select(ZaloAccount).order_by(ZaloAccount.name)).all()
    return [account_payload(account) for account in accounts]


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(
    body: AccountCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("accounts:write")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = ZaloAccount(
        name=body.name,
        cookie_cipher=request.app.state.secret_box.encrypt_json(body.cookies),
        imei_cipher=request.app.state.secret_box.encrypt_text(body.imei),
        user_agent=body.user_agent,
        listener_enabled=body.listener_enabled,
    )
    session.add(account)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Account name already exists") from exc
    session.refresh(account)
    write_audit(
        session,
        principal=principal,
        action="account.create",
        resource_type="zalo_account",
        resource_id=account.id,
        details={"name": account.name, "listener_enabled": account.listener_enabled},
        request=request,
    )
    return account_payload(account)


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: str,
    body: AccountUpdate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("accounts:write")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    changes = body.model_dump(exclude_unset=True)
    if "cookies" in changes:
        account.cookie_cipher = request.app.state.secret_box.encrypt_json(changes.pop("cookies"))
        account.status = "unchecked"
        account.last_error = None
        account.listener_enabled = False
        account.circuit_failures = 0
        account.circuit_open_until = None
    if "imei" in changes:
        account.imei_cipher = request.app.state.secret_box.encrypt_text(changes.pop("imei"))
        account.status = "unchecked"
        account.last_error = None
        account.listener_enabled = False
        account.circuit_failures = 0
        account.circuit_open_until = None
    for key, value in changes.items():
        setattr(account, key, value)
    session.commit()
    write_audit(
        session,
        principal=principal,
        action="account.update",
        resource_type="zalo_account",
        resource_id=account.id,
        details={"fields": sorted(body.model_fields_set)},
        request=request,
    )
    return account_payload(account)


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: str,
    body: AccountDelete,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("accounts:write")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if body.confirmation != account.name:
        raise HTTPException(status_code=422, detail="Account name confirmation does not match")

    running_jobs = int(
        session.scalar(
            select(func.count(Job.id)).where(
                Job.account_id == account_id,
                Job.status == "running",
            )
        )
        or 0
    )
    if running_jobs:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete an account while it has a running job",
        )

    job_count = int(
        session.scalar(select(func.count(Job.id)).where(Job.account_id == account_id)) or 0
    )
    event_count = int(
        session.scalar(select(func.count(Event.id)).where(Event.account_id == account_id)) or 0
    )
    now = utcnow()
    session.execute(
        update(Job)
        .where(Job.account_id == account_id, Job.status == "queued")
        .values(
            status="cancelled",
            error="Cancelled because the Zalo account was deleted",
            finished_at=now,
            locked_at=None,
            locked_by=None,
        )
    )
    session.execute(update(Job).where(Job.account_id == account_id).values(account_id=None))
    session.execute(update(Event).where(Event.account_id == account_id).values(account_id=None))
    account.listener_enabled = False
    account.enabled = False
    account_name = account.name
    session.delete(account)
    session.commit()
    write_audit(
        session,
        principal=principal,
        action="account.delete",
        resource_type="zalo_account",
        resource_id=account_id,
        details={
            "name": account_name,
            "retained_jobs": job_count,
            "retained_events": event_count,
        },
        request=request,
    )
    return {
        "deleted": True,
        "id": account_id,
        "name": account_name,
        "retained_jobs": job_count,
        "retained_events": event_count,
    }


@router.post("/accounts/{account_id}/test", status_code=status.HTTP_202_ACCEPTED)
def test_account(
    account_id: str,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("accounts:use")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    job = enqueue_job(
        session,
        account_id=account.id,
        action="__test_account__",
        payload={},
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=10,
    )
    write_audit(
        session,
        principal=principal,
        action="account.test",
        resource_type="zalo_account",
        resource_id=account.id,
        details={"job_id": job.id},
        request=request,
    )
    return {"job_id": job.id, "status": job.status}


@router.post("/accounts/{account_id}/listener/{enabled}")
def set_listener_state(
    account_id: str,
    enabled: bool,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("accounts:write")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if enabled and not account.enabled:
        raise HTTPException(status_code=409, detail="Enable the account before starting its listener")
    account.listener_enabled = enabled
    session.commit()
    write_audit(
        session,
        principal=principal,
        action="listener.enable" if enabled else "listener.disable",
        resource_type="zalo_account",
        resource_id=account.id,
        request=request,
    )
    return account_payload(account)


@router.post("/actions/{action}", status_code=status.HTTP_202_ACCEPTED)
def invoke_action(
    action: str,
    body: ActionInvoke,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    spec = get_action(action)
    if spec is None:
        raise HTTPException(status_code=404, detail="Unknown action")
    if spec.status == "unsupported":
        raise HTTPException(status_code=409, detail=spec.description or "Action is unsupported")
    if spec.dangerous and not principal.can("accounts:write"):
        raise HTTPException(
            status_code=403,
            detail="Dangerous actions require Admin or Super Admin permission",
        )
    if spec.dangerous and not secrets.compare_digest(body.confirmation or "", action):
        raise HTTPException(
            status_code=422,
            detail=f"Dangerous action requires confirmation exactly equal to '{action}'",
        )
    account = session.get(ZaloAccount, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    job = enqueue_job(
        session,
        account_id=account.id,
        action=action,
        payload=body.payload,
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=body.priority,
    )
    write_audit(
        session,
        principal=principal,
        action="job.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={"action": action, "account_id": account.id, "dangerous": spec.dangerous},
        request=request,
    )
    return {"job_id": job.id, "status": job.status}


@router.get("/jobs")
def list_jobs(
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
    job_status: str | None = Query(default=None, alias="status"),
    account_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    statement = select(Job).order_by(desc(Job.created_at)).limit(limit)
    if account_id:
        if session.get(ZaloAccount, account_id) is None:
            raise HTTPException(status_code=404, detail="Account not found")
        statement = statement.where(Job.account_id == account_id)
    if job_status:
        statement = statement.where(Job.status == job_status)
    return [job_payload(job) for job in session.scalars(statement).all()]


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> dict:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_payload(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_queued_job(
    job_id: str,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:manage")),
    _: None = Depends(verify_csrf),
) -> dict:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not cancel_job(session, job):
        raise HTTPException(status_code=409, detail="Only queued jobs can be cancelled")
    write_audit(
        session,
        principal=principal,
        action="job.cancel",
        resource_type="job",
        resource_id=job.id,
        request=request,
    )
    return job_payload(job)


@router.post("/jobs/{job_id}/retry")
def retry_failed_job(
    job_id: str,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:manage")),
    _: None = Depends(verify_csrf),
) -> dict:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not retry_job(session, job):
        raise HTTPException(status_code=409, detail="Only failed or cancelled jobs can be retried")
    write_audit(
        session,
        principal=principal,
        action="job.retry",
        resource_type="job",
        resource_id=job.id,
        request=request,
    )
    return job_payload(job)


@router.get("/chat/conversations")
def list_chat_conversations(
    account_id: str,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("read")),
    limit: int = Query(default=100, ge=1, le=250),
) -> list[dict]:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    conversations = conversation_payloads(
        session,
        account,
        user_id=principal.id if principal.kind == "user" else None,
        limit=limit,
    )
    latest_contacts = session.scalar(
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.action == "fetchAllFriends",
            Job.status == "succeeded",
        )
        .order_by(desc(Job.finished_at), desc(Job.created_at))
        .limit(1)
    )
    contacts_by_id = {
        contact["user_id"]: contact
        for contact in _normalize_contacts(
            latest_contacts.result if latest_contacts else None
        )
    }
    for conversation in conversations:
        if conversation["thread_type"] != "user":
            continue
        contact = contacts_by_id.get(conversation["thread_id"])
        if contact is None:
            continue
        conversation["name"] = contact["name"]
        conversation["initials"] = contact["initials"]
        conversation["avatar_url"] = (
            contact["avatar_url"] or conversation.get("avatar_url")
        )
        conversation["profile_cached"] = True
    return conversations


@router.get("/chat/contacts")
def list_chat_contacts(
    account_id: str,
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    latest_job = session.scalar(
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.action == "fetchAllFriends",
        )
        .order_by(desc(Job.created_at))
        .limit(1)
    )
    latest_success = session.scalar(
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.action == "fetchAllFriends",
            Job.status == "succeeded",
        )
        .order_by(desc(Job.finished_at), desc(Job.created_at))
        .limit(1)
    )
    if latest_success and backfill_friend_profiles(
        session,
        account_id=account_id,
        result=latest_success.result,
    ):
        session.commit()
    contacts = _normalize_contacts(latest_success.result if latest_success else None)
    profile_cache = cached_profiles(
        session,
        account_id=account_id,
        user_ids={item["user_id"] for item in contacts},
    )
    for contact in contacts:
        profile = profile_cache.get(contact["user_id"])
        if profile:
            contact.update(_contact_profile_payload(profile, contact["user_id"]))
        elif contact.get("relationship_status") == "unknown":
            contact["relationship_status"] = "friend"
            contact["blocked_by_me"] = False
    return {
        "contacts": contacts,
        "last_sync_at": latest_success.finished_at if latest_success else None,
        "job": _contacts_job_payload(latest_job),
    }


@router.get("/chat/friends/{user_id}")
def get_chat_friend_relationship(
    user_id: str,
    account_id: str,
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    user_id = user_id.strip()
    if not user_id or len(user_id) > 100:
        raise HTTPException(status_code=422, detail="User ID is invalid")
    profile = session.get(ZaloContactProfile, (account_id, user_id))
    if profile is None:
        latest_success = session.scalar(
            select(Job)
            .where(
                Job.account_id == account_id,
                Job.action == "fetchAllFriends",
                Job.status == "succeeded",
            )
            .order_by(desc(Job.finished_at), desc(Job.created_at))
            .limit(1)
        )
        contact = next(
            (
                item
                for item in _normalize_contacts(
                    latest_success.result if latest_success else None
                )
                if item["user_id"] == user_id
            ),
            None,
        )
        if contact:
            contact["relationship_status"] = "friend"
            contact["blocked_by_me"] = False
            contact["synced_at"] = (
                latest_success.finished_at if latest_success else None
            )
            return contact
    return _contact_profile_payload(profile, user_id)


@router.post(
    "/chat/friends/{user_id}/actions",
    status_code=status.HTTP_202_ACCEPTED,
)
def manage_chat_friend_relationship(
    user_id: str,
    body: ChatFriendActionCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("accounts:write")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = _require_chat_account(session, body.account_id)
    user_id = user_id.strip()
    if not user_id or len(user_id) > 100:
        raise HTTPException(status_code=422, detail="User ID is invalid")
    if user_id == account.zalo_uid:
        raise HTTPException(status_code=409, detail="Không thể quản lý quan hệ với chính tài khoản này")

    profile = session.get(ZaloContactProfile, (account.id, user_id))
    display_name = (
        profile.name
        if profile
        else body.display_name or f"Zalo {user_id[-6:]}"
    )
    action_map = {
        "send_request": "sendFriendRequest",
        "accept_request": "acceptFriendRequest",
        "block": "blockUser",
        "unblock": "unblockUser",
    }
    connector_action = action_map[body.action]
    relationship_status = profile.relationship_status if profile else "unknown"
    if body.action == "send_request" and relationship_status in {
        "friend",
        "pending_outgoing",
        "blocked",
    }:
        raise HTTPException(status_code=409, detail="Quan hệ này không thể gửi thêm lời mời kết bạn")
    if body.action == "accept_request" and relationship_status in {"friend", "blocked"}:
        raise HTTPException(
            status_code=409,
            detail="Quan hệ này không thể chấp nhận lời mời kết bạn",
        )
    if body.action == "block" and (relationship_status == "blocked" or (profile and profile.blocked_by_me)):
        raise HTTPException(status_code=409, detail="Tài khoản này đã bị chặn")
    if body.action == "unblock" and relationship_status != "blocked" and not (profile and profile.blocked_by_me):
        raise HTTPException(status_code=409, detail="Tài khoản này chưa bị chặn")
    if body.action in {"block", "unblock"} and (
        not body.confirmed
        or not secrets.compare_digest(
            (body.confirmation or "").encode("utf-8"),
            display_name.encode("utf-8"),
        )
    ):
        raise HTTPException(
            status_code=422,
            detail=f"Nhập chính xác tên '{display_name}' và xác nhận cảnh báo để tiếp tục",
        )

    payload: dict[str, Any] = {
        "userId": user_id,
        "_chat_context": {
            "friend_action": body.action,
            "display_name": display_name,
        },
    }
    if body.action == "send_request":
        payload["msg"] = body.message or "Xin chào, mình muốn kết bạn với bạn."
    active_jobs = session.scalars(
        select(Job)
        .where(
            Job.account_id == account.id,
            Job.action == connector_action,
            Job.status.in_(("queued", "running")),
        )
        .order_by(desc(Job.created_at))
        .limit(50)
    ).all()
    active_job = next(
        (
            job
            for job in active_jobs
            if str((job.payload or {}).get("userId") or "") == user_id
        ),
        None,
    )
    if active_job:
        return {
            "job_id": active_job.id,
            "status": active_job.status,
            "action": connector_action,
            "deduplicated": True,
            "profile": _contact_profile_payload(profile, user_id),
        }

    job = enqueue_job(
        session,
        account_id=account.id,
        action=connector_action,
        payload=payload,
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=35,
        max_attempts=1,
    )
    write_audit(
        session,
        principal=principal,
        action=f"chat.friend.{body.action}.enqueue",
        resource_type="zalo_contact_profile",
        resource_id=f"{account.id}:{user_id}",
        details={
            "account_id": account.id,
            "user_id": user_id,
            "display_name": display_name,
            "job_id": job.id,
            "connector_action": connector_action,
        },
        request=request,
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "action": connector_action,
        "deduplicated": False,
        "profile": _contact_profile_payload(profile, user_id),
    }


@router.get("/chat/stickers")
def list_chat_stickers(
    account_id: str,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("read")),
) -> dict:
    if session.get(ZaloAccount, account_id) is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return _chat_sticker_catalog(
        session,
        account_id=account_id,
        principal=principal,
    )


@router.put("/chat/stickers/preferences")
def update_chat_sticker_preferences(
    body: ChatStickerPreferencesUpdate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    if session.get(ZaloAccount, body.account_id) is None:
        raise HTTPException(status_code=404, detail="Account not found")
    favorites: list[dict] = []
    seen: set[str] = set()
    for value in body.favorites:
        sticker = _sticker_snapshot(value.model_dump())
        if sticker is None or sticker["key"] in seen:
            continue
        favorites.append(
            {
                key: sticker[key]
                for key in (
                    "sticker_id",
                    "category_id",
                    "sticker_type",
                    "thumbnail_url",
                    "width",
                    "height",
                )
            }
        )
        seen.add(sticker["key"])

    setting_key = _sticker_setting_key(principal, body.account_id)
    setting = session.get(AppSetting, setting_key)
    if setting is None:
        setting = AppSetting(
            key=setting_key,
            value={"favorites": favorites},
            updated_by_id=principal.id if principal.kind == "user" else None,
        )
        session.add(setting)
    else:
        setting.value = {"favorites": favorites}
        setting.updated_by_id = principal.id if principal.kind == "user" else None
    session.commit()
    write_audit(
        session,
        principal=principal,
        action="chat.sticker_preferences.update",
        resource_type="app_setting",
        resource_id=setting_key,
        details={
            "account_id": body.account_id,
            "favorite_count": len(favorites),
        },
        request=request,
    )
    return _chat_sticker_catalog(
        session,
        account_id=body.account_id,
        principal=principal,
    )


@router.get("/chat/gifs")
def list_chat_gifs(
    request: Request,
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> dict:
    settings = request.app.state.settings
    items = session.scalars(
        select(GifLibraryItem)
        .order_by(
            desc(GifLibraryItem.favorite),
            desc(GifLibraryItem.last_used_at),
            desc(GifLibraryItem.created_at),
        )
        .limit(settings.gif_library_max_items)
    ).all()
    used_items, used_bytes = _gif_library_usage(session)
    return {
        "items": [
            _gif_library_item_payload(item, settings)
            for item in items
        ],
        "favorite_count": sum(1 for item in items if item.favorite),
        "quota": {
            "used_items": used_items,
            "max_items": settings.gif_library_max_items,
            "used_bytes": used_bytes,
            "max_bytes": settings.gif_library_max_bytes,
        },
        "shared": True,
    }


@router.post("/chat/gifs/import", status_code=status.HTTP_201_CREATED)
def import_chat_gif(
    body: ChatGifImportCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    settings = request.app.state.settings
    gif_id = _giphy_gif_id(body.url)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    temporary_path = settings.upload_dir / f"giphy-{secrets.token_hex(16)}.gif"
    max_bytes = settings.max_upload_mb * 1024 * 1024
    try:
        size = _download_giphy_gif(
            gif_id=gif_id,
            target=temporary_path,
            max_bytes=max_bytes,
            timeout_seconds=settings.request_timeout_seconds,
        )
        width, height = _gif_dimensions(temporary_path)
        transient_upload = MediaUpload(
            original_name=f"giphy-{gif_id}.gif",
            stored_path=str(temporary_path),
            size_bytes=size,
            content_type="image/gif",
            created_by_id=principal.id if principal.kind == "user" else None,
            expires_at=utcnow() + timedelta(minutes=5),
        )
        item, created = _persist_gif_upload(
            session,
            upload=transient_upload,
            settings=settings,
            created_by_id=principal.id if principal.kind == "user" else None,
            width=width,
            height=height,
            mark_used=False,
        )
        session.commit()
    finally:
        temporary_path.unlink(missing_ok=True)

    write_audit(
        session,
        principal=principal,
        action="chat.gif_library.import_giphy",
        resource_type="gif_library_item",
        resource_id=item.id,
        details={
            "source": "giphy",
            "giphy_id": gif_id,
            "source_url": body.url,
            "name": item.original_name,
            "size_bytes": item.size_bytes,
            "created": created,
        },
        request=request,
    )
    used_items, used_bytes = _gif_library_usage(session)
    return {
        "item": _gif_library_item_payload(item, settings),
        "created": created,
        "quota": {
            "used_items": used_items,
            "max_items": settings.gif_library_max_items,
            "used_bytes": used_bytes,
            "max_bytes": settings.gif_library_max_bytes,
        },
    }


@router.put("/chat/gifs/{gif_id}")
def update_chat_gif(
    gif_id: str,
    body: ChatGifLibraryUpdate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    item = session.get(GifLibraryItem, gif_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy GIF trong thư viện")
    item.favorite = body.favorite
    item.updated_at = utcnow()
    session.commit()
    write_audit(
        session,
        principal=principal,
        action="chat.gif_library.favorite",
        resource_type="gif_library_item",
        resource_id=item.id,
        details={
            "name": item.original_name,
            "favorite": item.favorite,
        },
        request=request,
    )
    return _gif_library_item_payload(item, request.app.state.settings)


@router.delete("/chat/gifs/{gif_id}")
def delete_chat_gif(
    gif_id: str,
    body: ChatGifLibraryDelete,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("accounts:write")),
    _: None = Depends(verify_csrf),
) -> dict:
    item = session.get(GifLibraryItem, gif_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy GIF trong thư viện")
    if body.confirmation != item.original_name:
        raise HTTPException(
            status_code=422,
            detail="Tên GIF xác nhận không khớp",
        )
    active_jobs = session.scalars(
        select(Job).where(
            Job.action == "__chat_rich_media__",
            Job.status.in_(("queued", "running")),
        )
    ).all()
    if any(
        str(((job.payload or {}).get("media") or {}).get("library_gif_id") or "")
        == item.id
        for job in active_jobs
    ):
        raise HTTPException(
            status_code=409,
            detail="GIF đang được một job sử dụng; hãy chờ job hoàn tất rồi xóa",
        )
    path = Path(item.stored_path)
    item_id = item.id
    item_name = item.original_name
    item_size = item.size_bytes
    session.delete(item)
    session.commit()
    if safe_child_path(request.app.state.settings.gif_library_dir, path):
        path.unlink(missing_ok=True)
    write_audit(
        session,
        principal=principal,
        action="chat.gif_library.delete",
        resource_type="gif_library_item",
        resource_id=item_id,
        details={"name": item_name, "size_bytes": item_size},
        request=request,
    )
    return {"deleted": True, "id": item_id, "name": item_name}


@router.get("/chat/groups/{thread_id}")
def get_chat_group_info(
    thread_id: str,
    account_id: str,
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    metadata = group_metadata(session, account_id).get(thread_id)
    all_jobs = _group_info_jobs(session, account_id, thread_id)
    latest_success = next(
        (job for job in all_jobs if job.status == "succeeded"),
        None,
    )
    latest_job = all_jobs[0] if all_jobs else None
    snapshot = _group_info_snapshot(
        latest_success.result if latest_success else None,
        thread_id=thread_id,
        fallback=metadata,
    )
    return {
        **snapshot,
        "last_sync_at": latest_success.finished_at if latest_success else None,
        "job": _group_info_job_payload(latest_job),
    }


@router.post("/chat/groups/{thread_id}/sync", status_code=status.HTTP_202_ACCEPTED)
def sync_chat_group_info(
    thread_id: str,
    account_id: str,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    _require_chat_account(session, account_id)
    active_job = next(
        (
            job
            for job in _group_info_jobs(session, account_id, thread_id)
            if job.status in {"queued", "running"}
        ),
        None,
    )
    if active_job is not None:
        return {
            "job_id": active_job.id,
            "status": active_job.status,
            "deduplicated": True,
        }
    job = enqueue_job(
        session,
        account_id=account.id,
        action="__chat_group_info__",
        payload={"thread_id": thread_id, "groupId": thread_id},
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=65,
        max_attempts=2,
    )
    write_audit(
        session,
        principal=principal,
        action="chat.group_info.sync.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={"account_id": account.id, "thread_id": thread_id},
        request=request,
    )
    return {"job_id": job.id, "status": job.status, "deduplicated": False}


@router.get("/chat/groups/{thread_id}/pins")
def get_chat_group_pins(
    thread_id: str,
    account_id: str,
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    all_jobs = _group_pin_jobs(session, account_id, thread_id)
    latest_success = next(
        (job for job in all_jobs if job.status == "succeeded"),
        None,
    )
    latest_job = all_jobs[0] if all_jobs else None
    return {
        "thread_id": thread_id,
        "pins": _group_pins_snapshot(
            session,
            latest_success.result if latest_success else None,
            account_id=account_id,
            thread_id=thread_id,
        ),
        "last_sync_at": latest_success.finished_at if latest_success else None,
        "job": _group_info_job_payload(latest_job),
    }


@router.post(
    "/chat/groups/{thread_id}/pins/sync",
    status_code=status.HTTP_202_ACCEPTED,
)
def sync_chat_group_pins(
    thread_id: str,
    account_id: str,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = _require_chat_account(session, account_id)
    active_job = next(
        (
            job
            for job in _group_pin_jobs(session, account.id, thread_id)
            if job.status in {"queued", "running"}
        ),
        None,
    )
    if active_job is not None:
        return {
            "job_id": active_job.id,
            "status": active_job.status,
            "deduplicated": True,
        }
    job = enqueue_job(
        session,
        account_id=account.id,
        action="__chat_group_pins__",
        payload={"thread_id": thread_id, "groupId": thread_id},
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=67,
        max_attempts=2,
    )
    write_audit(
        session,
        principal=principal,
        action="chat.group_pins.sync.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={"account_id": account.id, "thread_id": thread_id},
        request=request,
    )
    return {"job_id": job.id, "status": job.status, "deduplicated": False}


@router.get("/chat/groups/{thread_id}/notes")
def get_chat_group_notes(
    thread_id: str,
    account_id: str,
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    all_jobs = _group_note_jobs(session, account_id, thread_id)
    latest_success = next(
        (job for job in all_jobs if job.status == "succeeded"),
        None,
    )
    latest_job = all_jobs[0] if all_jobs else None
    return {
        "thread_id": thread_id,
        "notes": _group_notes_snapshot(
            latest_success.result if latest_success else None,
        ),
        "last_sync_at": latest_success.finished_at if latest_success else None,
        "job": _group_info_job_payload(latest_job),
    }


@router.post(
    "/chat/groups/{thread_id}/notes/sync",
    status_code=status.HTTP_202_ACCEPTED,
)
def sync_chat_group_notes(
    thread_id: str,
    account_id: str,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = _require_chat_account(session, account_id)
    active_job = next(
        (
            job
            for job in _group_note_jobs(session, account.id, thread_id)
            if job.status in {"queued", "running"}
        ),
        None,
    )
    if active_job is not None:
        return {
            "job_id": active_job.id,
            "status": active_job.status,
            "deduplicated": True,
        }
    job = enqueue_job(
        session,
        account_id=account.id,
        action="__chat_group_notes__",
        payload={"thread_id": thread_id, "groupId": thread_id},
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=68,
        max_attempts=2,
    )
    write_audit(
        session,
        principal=principal,
        action="chat.group_notes.sync.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={"account_id": account.id, "thread_id": thread_id},
        request=request,
    )
    return {"job_id": job.id, "status": job.status, "deduplicated": False}


@router.get("/chat/groups/{thread_id}/polls")
def get_chat_group_polls(
    thread_id: str,
    account_id: str,
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    all_jobs = _group_poll_jobs(session, account_id, thread_id)
    latest_success = next(
        (job for job in all_jobs if job.status == "succeeded"),
        None,
    )
    latest_job = all_jobs[0] if all_jobs else None
    return {
        "thread_id": thread_id,
        "polls": _group_polls_snapshot(
            latest_success.result if latest_success else None,
        ),
        "last_sync_at": latest_success.finished_at if latest_success else None,
        "job": _group_info_job_payload(latest_job),
    }


@router.post(
    "/chat/groups/{thread_id}/polls/sync",
    status_code=status.HTTP_202_ACCEPTED,
)
def sync_chat_group_polls(
    thread_id: str,
    account_id: str,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = _require_chat_account(session, account_id)
    active_job = next(
        (
            job
            for job in _group_poll_jobs(session, account.id, thread_id)
            if job.status in {"queued", "running"}
        ),
        None,
    )
    if active_job is not None:
        return {
            "job_id": active_job.id,
            "status": active_job.status,
            "deduplicated": True,
        }
    job = enqueue_job(
        session,
        account_id=account.id,
        action="__chat_group_polls__",
        payload={"thread_id": thread_id, "groupId": thread_id},
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=69,
        max_attempts=2,
    )
    write_audit(
        session,
        principal=principal,
        action="chat.group_polls.sync.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={"account_id": account.id, "thread_id": thread_id},
        request=request,
    )
    return {"job_id": job.id, "status": job.status, "deduplicated": False}


@router.get("/chat/groups/{thread_id}/polls/{poll_id}")
def get_chat_group_poll_detail(
    thread_id: str,
    poll_id: str,
    account_id: str,
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    detail_jobs = _group_poll_detail_jobs(session, account_id, thread_id, poll_id)
    latest_detail = next(
        (job for job in detail_jobs if job.status == "succeeded"),
        None,
    )
    latest_job = detail_jobs[0] if detail_jobs else None
    poll = _group_poll_detail_snapshot(
        latest_detail.result if latest_detail else None,
        poll_id,
    )
    if poll is None:
        list_jobs = _group_poll_jobs(session, account_id, thread_id)
        list_success = next(
            (job for job in list_jobs if job.status == "succeeded"),
            None,
        )
        listed = _group_polls_snapshot(list_success.result if list_success else None)
        poll = next((item for item in listed if item["poll_id"] == str(poll_id)), None)
    return {
        "thread_id": thread_id,
        "poll_id": poll_id,
        "poll": poll,
        "last_sync_at": latest_detail.finished_at if latest_detail else None,
        "job": _group_info_job_payload(latest_job),
    }


@router.post(
    "/chat/groups/{thread_id}/polls/{poll_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
)
def sync_chat_group_poll_detail(
    thread_id: str,
    poll_id: str,
    account_id: str,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = _require_chat_account(session, account_id)
    active_job = next(
        (
            job
            for job in _group_poll_detail_jobs(session, account.id, thread_id, poll_id)
            if job.status in {"queued", "running"}
        ),
        None,
    )
    if active_job is not None:
        return {
            "job_id": active_job.id,
            "status": active_job.status,
            "deduplicated": True,
        }
    job = enqueue_job(
        session,
        account_id=account.id,
        action="__chat_poll_detail__",
        payload={
            "thread_id": thread_id,
            "groupId": thread_id,
            "poll_id": poll_id,
            "pollId": poll_id,
        },
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=70,
        max_attempts=2,
    )
    write_audit(
        session,
        principal=principal,
        action="chat.poll_detail.sync.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={
            "account_id": account.id,
            "thread_id": thread_id,
            "poll_id": poll_id,
        },
        request=request,
    )
    return {"job_id": job.id, "status": job.status, "deduplicated": False}


@router.post("/chat/groups/{thread_id}/rename", status_code=status.HTTP_202_ACCEPTED)
def rename_chat_group(
    thread_id: str,
    body: ChatGroupRenameCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("accounts:write")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = _require_chat_account(session, body.account_id)
    latest_success = next(
        iter(_group_info_jobs(session, account.id, thread_id, status_filter="succeeded")),
        None,
    )
    metadata = group_metadata(session, account.id).get(thread_id)
    snapshot = _group_info_snapshot(
        latest_success.result if latest_success else None,
        thread_id=thread_id,
        fallback=metadata,
    )
    current_name = str(snapshot["group"]["name"])
    if body.current_name != current_name:
        raise HTTPException(
            status_code=422,
            detail="Tên nhóm xác nhận không khớp với tên hiện tại",
        )
    if body.new_name == current_name:
        raise HTTPException(status_code=409, detail="Tên nhóm mới không thay đổi")
    active_job = _active_group_management_job(session, account.id, thread_id)
    if active_job is not None:
        raise HTTPException(
            status_code=409,
            detail="Nhóm đang có một tác vụ quản lý chưa hoàn tất",
        )
    job = enqueue_job(
        session,
        account_id=account.id,
        action="changeGroupName",
        payload={
            "groupName": body.new_name,
            "groupId": thread_id,
            "_chat_context": {
                "group_management": True,
                "previous_name": current_name,
            },
        },
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=40,
        max_attempts=1,
    )
    write_audit(
        session,
        principal=principal,
        action="chat.group.rename.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={
            "account_id": account.id,
            "thread_id": thread_id,
            "previous_name": current_name,
            "new_name": body.new_name,
        },
        request=request,
    )
    return {"job_id": job.id, "status": job.status}


@router.post("/chat/groups/{thread_id}/invite", status_code=status.HTTP_202_ACCEPTED)
def invite_chat_group_members(
    thread_id: str,
    body: ChatGroupInviteCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("accounts:write")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = _require_chat_account(session, body.account_id)
    active_job = _active_group_management_job(session, account.id, thread_id)
    if active_job is not None:
        raise HTTPException(
            status_code=409,
            detail="Nhóm đang có một tác vụ quản lý chưa hoàn tất",
        )
    latest_success = next(
        iter(_group_info_jobs(session, account.id, thread_id, status_filter="succeeded")),
        None,
    )
    snapshot = _group_info_snapshot(
        latest_success.result if latest_success else None,
        thread_id=thread_id,
        fallback=group_metadata(session, account.id).get(thread_id),
    )
    existing_ids = {
        str(member["user_id"])
        for member in snapshot["members"]
        if member.get("user_id")
    }
    invite_ids = [member_id for member_id in body.user_ids if member_id not in existing_ids]
    if not invite_ids:
        raise HTTPException(
            status_code=409,
            detail="Các tài khoản đã chọn đều đang ở trong nhóm",
        )
    job = enqueue_job(
        session,
        account_id=account.id,
        action="addUsersToGroup",
        payload={
            "user_ids": invite_ids,
            "groupId": thread_id,
            "_chat_context": {"group_management": True},
        },
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=45,
        max_attempts=1,
    )
    write_audit(
        session,
        principal=principal,
        action="chat.group.members.invite.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={
            "account_id": account.id,
            "thread_id": thread_id,
            "member_count": len(invite_ids),
        },
        request=request,
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "invited_user_ids": invite_ids,
        "skipped_existing": len(body.user_ids) - len(invite_ids),
    }


@router.post("/chat/profiles/sync", status_code=status.HTTP_202_ACCEPTED)
def sync_chat_profiles(
    body: ChatProfileSyncCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = _require_chat_account(session, body.account_id)
    requested_ids = [
        user_id
        for user_id in body.user_ids
        if user_id != account.zalo_uid
    ]
    cached_ids = set(
        session.scalars(
            select(ZaloContactProfile.user_id).where(
                ZaloContactProfile.account_id == account.id,
                ZaloContactProfile.user_id.in_(tuple(requested_ids)),
            )
        ).all()
    ) if requested_ids else set()
    missing_ids = [
        user_id
        for user_id in requested_ids
        if user_id not in cached_ids
    ]
    if not missing_ids:
        return {
            "job_id": None,
            "status": "succeeded",
            "deduplicated": True,
            "user_ids": requested_ids,
        }

    queued_job = session.scalar(
        select(Job)
        .where(
            Job.account_id == account.id,
            Job.action == "__chat_profiles__",
            Job.status == "queued",
        )
        .order_by(desc(Job.created_at))
        .limit(1)
    )
    if queued_job is not None:
        existing_ids = {
            str(value).strip()
            for value in (queued_job.payload or {}).get("user_ids", [])
            if str(value).strip()
        }
        merged_ids = list(dict.fromkeys([*existing_ids, *missing_ids]))[:100]
        queued_job.payload = {"user_ids": merged_ids}
        session.commit()
        return {
            "job_id": queued_job.id,
            "status": queued_job.status,
            "deduplicated": True,
            "user_ids": merged_ids,
        }

    running_jobs = session.scalars(
        select(Job)
        .where(
            Job.account_id == account.id,
            Job.action == "__chat_profiles__",
            Job.status == "running",
        )
        .order_by(desc(Job.created_at))
        .limit(3)
    ).all()
    for running_job in running_jobs:
        active_ids = {
            str(value).strip()
            for value in (running_job.payload or {}).get("user_ids", [])
            if str(value).strip()
        }
        if set(missing_ids).issubset(active_ids):
            return {
                "job_id": running_job.id,
                "status": running_job.status,
                "deduplicated": True,
                "user_ids": sorted(active_ids),
            }

    job = enqueue_job(
        session,
        account_id=account.id,
        action="__chat_profiles__",
        payload={"user_ids": missing_ids},
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=65,
        max_attempts=2,
    )
    write_audit(
        session,
        principal=principal,
        action="chat.profiles.sync.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={
            "account_id": account.id,
            "profile_count": len(missing_ids),
        },
        request=request,
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "deduplicated": False,
        "user_ids": missing_ids,
    }


@router.post("/chat/contacts/sync", status_code=status.HTTP_202_ACCEPTED)
def sync_chat_contacts(
    account_id: str,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    _require_chat_account(session, account_id)
    active_job = session.scalar(
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.action == "fetchAllFriends",
            Job.status.in_(("queued", "running")),
        )
        .order_by(desc(Job.created_at))
        .limit(1)
    )
    if active_job is not None:
        return {
            "job_id": active_job.id,
            "status": active_job.status,
            "deduplicated": True,
        }
    job = enqueue_job(
        session,
        account_id=account.id,
        action="fetchAllFriends",
        payload={},
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=70,
    )
    write_audit(
        session,
        principal=principal,
        action="contacts.sync.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={"account_id": account.id, "action": "fetchAllFriends"},
        request=request,
    )
    return {"job_id": job.id, "status": job.status, "deduplicated": False}


@router.get("/chat/messages")
def list_chat_messages(
    account_id: str,
    thread_id: str = Query(min_length=1, max_length=100),
    around_event_id: int | None = Query(default=None, ge=1),
    before_event_id: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("read")),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict]:
    if around_event_id is not None and before_event_id is not None:
        raise HTTPException(
            status_code=422,
            detail="Chỉ được dùng một cursor: around_event_id hoặc before_event_id",
        )
    account = session.get(ZaloAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return message_payloads(
        session,
        account,
        selected_thread_id=thread_id,
        around_event_id=around_event_id,
        before_event_id=before_event_id,
        user_id=principal.id if principal.kind == "user" else None,
        limit=limit,
    )


@router.post("/chat/read-state")
def mark_chat_read(
    body: ChatReadStateUpdate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("read")),
    _: None = Depends(verify_csrf),
) -> dict:
    if principal.kind != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Read state is only available for signed-in users",
        )
    account = session.get(ZaloAccount, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    latest_event_id = int(
        session.scalar(
            select(func.max(Event.id)).where(
                Event.account_id == account.id,
                Event.event_type == "message.received",
                Event.thread_id == body.thread_id,
            )
        )
        or 0
    )
    identity = (principal.id, account.id, body.thread_id)
    read_state = session.get(ConversationRead, identity)
    if read_state is None:
        read_state = ConversationRead(
            user_id=principal.id,
            account_id=account.id,
            thread_id=body.thread_id,
            last_read_event_id=latest_event_id,
        )
        session.add(read_state)
    else:
        read_state.last_read_event_id = max(
            read_state.last_read_event_id,
            latest_event_id,
        )
        read_state.updated_at = utcnow()
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        read_state = session.scalar(
            select(ConversationRead)
            .where(
                ConversationRead.user_id == principal.id,
                ConversationRead.account_id == account.id,
                ConversationRead.thread_id == body.thread_id,
            )
            .with_for_update()
        )
        if read_state is None:
            raise
        read_state.last_read_event_id = max(
            read_state.last_read_event_id,
            latest_event_id,
        )
        read_state.updated_at = utcnow()
        session.commit()
    return {
        "account_id": account.id,
        "thread_id": body.thread_id,
        "last_read_event_id": read_state.last_read_event_id,
    }


@router.post("/chat/messages", status_code=status.HTTP_202_ACCEPTED)
def send_chat_message(
    body: ChatMessageCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = _require_chat_account(session, body.account_id)
    action = "sendMessage"
    payload = {
        "message": {"text": body.text},
        "thread_id": body.thread_id,
        "thread_type": body.thread_type,
    }
    if body.reply_event_id is not None:
        if not body.text:
            raise HTTPException(
                status_code=422,
                detail="Hãy nhập nội dung khi trả lời kèm media để Zalo giữ đúng trích dẫn",
            )
        source = _chat_source_event(
            session,
            account_id=account.id,
            event_id=body.reply_event_id,
        )
        if source.thread_id != body.thread_id:
            raise HTTPException(
                status_code=409,
                detail="Tin nhắn trả lời không thuộc hội thoại đang mở",
            )
        source_message = incoming_message(source, account)
        reference = message_reference(source.payload or {})
        if not reference.get("msg_id") or not reference.get("cli_msg_id"):
            raise HTTPException(
                status_code=409,
                detail="Tin nhắn này thiếu ID để Zalo tạo trả lời trích dẫn",
            )
        action = "replyMessage"
        payload["replyMsg"] = (source.payload or {}).get("message_object") or {}
        payload["_chat_context"] = {
            "reply": {
                "message_id": reference.get("msg_id"),
                "cli_msg_id": reference.get("cli_msg_id"),
                "author_id": source_message.get("author_id"),
                "author_name": source_message.get("author_name"),
                "text": source_message.get("text"),
                "msg_type": reference.get("msg_type"),
            }
        }
    job = enqueue_job(
        session,
        account_id=account.id,
        action=action,
        payload=payload,
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=50,
    )
    write_audit(
        session,
        principal=principal,
        action="chat.message.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={
            "account_id": account.id,
            "thread_id": body.thread_id,
            "thread_type": body.thread_type,
            "text_length": len(body.text),
            "reply_event_id": body.reply_event_id,
        },
        request=request,
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "message": outgoing_message(job),
    }


@router.post("/chat/media", status_code=status.HTTP_202_ACCEPTED)
def send_chat_media(
    body: ChatMediaCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = _require_chat_account(session, body.account_id)
    media_items = []
    for item in body.items:
        upload_id = item.upload_token.removeprefix("upload:")
        upload = session.get(MediaUpload, upload_id)
        if upload is None or is_expired(upload.expires_at):
            raise HTTPException(status_code=422, detail=f"Upload {item.name} đã hết hạn")
        if principal.kind == "user" and upload.created_by_id != principal.id:
            raise HTTPException(status_code=403, detail="Không được dùng upload của người khác")
        if principal.kind != "user" and upload.created_by_id is not None:
            raise HTTPException(status_code=403, detail="Upload không thuộc API key này")
        if upload.size_bytes != item.size_bytes:
            raise HTTPException(status_code=422, detail=f"Kích thước {item.name} không hợp lệ")
        try:
            kind = validate_media_kind(
                upload.original_name,
                upload.content_type,
                item.kind,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        filename = safe_upload_name(upload.original_name)
        media_items.append(
            {
                "upload_token": item.upload_token,
                "kind": kind,
                "filename": filename,
                "content_type": upload.content_type or item.content_type,
                "size": upload.size_bytes,
                "width": item.width,
                "height": item.height,
                "duration_ms": item.duration_ms,
                "extension": Path(filename).suffix.lstrip(".").lower() or "bin",
                "url": outbound_media_url(request.app.state.settings, upload.id),
            }
        )

    context: dict = {
        "kind": media_items[0]["kind"] if len(media_items) == 1 else "media",
        "media": (
            {**media_items[0], "items": media_items}
            if len(media_items) == 1
            else {"items": media_items}
        ),
    }
    payload: dict = {
        "thread_id": body.thread_id,
        "thread_type": body.thread_type,
        "text": body.text,
        "items": media_items,
        "_chat_context": context,
    }
    if body.reply_event_id is not None:
        source = _chat_source_event(
            session,
            account_id=account.id,
            event_id=body.reply_event_id,
        )
        if source.thread_id != body.thread_id:
            raise HTTPException(
                status_code=409,
                detail="Tin nhắn trả lời không thuộc hội thoại đang mở",
            )
        source_message = incoming_message(source, account)
        reference = message_reference(source.payload or {})
        if not reference.get("msg_id") or not reference.get("cli_msg_id"):
            raise HTTPException(
                status_code=409,
                detail="Tin nhắn này thiếu ID để Zalo tạo trả lời trích dẫn",
            )
        payload["replyMsg"] = (source.payload or {}).get("message_object") or {}
        context["reply"] = {
            "message_id": reference.get("msg_id"),
            "cli_msg_id": reference.get("cli_msg_id"),
            "author_id": source_message.get("author_id"),
            "author_name": source_message.get("author_name"),
            "text": source_message.get("text"),
            "msg_type": reference.get("msg_type"),
        }

    job = enqueue_job(
        session,
        account_id=account.id,
        action="__chat_media__",
        payload=payload,
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=45,
        max_attempts=1,
    )
    write_audit(
        session,
        principal=principal,
        action="chat.media.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={
            "account_id": account.id,
            "thread_id": body.thread_id,
            "thread_type": body.thread_type,
            "item_count": len(media_items),
            "kinds": [item["kind"] for item in media_items],
            "caption_length": len(body.text),
            "reply_event_id": body.reply_event_id,
        },
        request=request,
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "message": outgoing_message(job),
    }


@router.post("/chat/rich-media", status_code=status.HTTP_202_ACCEPTED)
def send_chat_rich_media(
    body: ChatRichMediaCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = _require_chat_account(session, body.account_id)
    media: dict[str, Any] = {"kind": body.kind}
    created_library_gif: GifLibraryItem | None = None
    if body.kind == "sticker":
        try:
            sticker_id = str(int(body.sticker_id or "0"))
            category_id = str(int(body.category_id or "0"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Sticker ID và category ID phải là số") from exc
        if sticker_id == "0" or category_id == "0":
            raise HTTPException(status_code=422, detail="Sticker cần sticker_id và category_id")
        media.update(
            {
                "sticker_id": sticker_id,
                "category_id": category_id,
                "sticker_type": body.sticker_type,
                "url": body.thumbnail_url,
                "thumbnail_url": body.thumbnail_url,
                "width": body.width or 130,
                "height": body.height or 130,
            }
        )
    elif body.kind == "gif":
        if body.gif_library_id and body.upload_token:
            raise HTTPException(
                status_code=422,
                detail="Chỉ được chọn một GIF trong thư viện hoặc một tệp tải lên",
            )
        settings = request.app.state.settings
        if body.gif_library_id:
            library_gif = session.get(GifLibraryItem, body.gif_library_id)
            if library_gif is None:
                raise HTTPException(
                    status_code=404,
                    detail="GIF đã chọn không còn trong thư viện",
                )
            path = Path(library_gif.stored_path)
            if not safe_child_path(settings.gif_library_dir, path) or not path.is_file():
                raise HTTPException(
                    status_code=404,
                    detail="Tệp GIF trong thư viện không còn tồn tại",
                )
            library_gif.use_count += 1
            library_gif.last_used_at = utcnow()
            media.update(
                {
                    "library_gif_id": library_gif.id,
                    "filename": library_gif.original_name,
                    "size": library_gif.size_bytes,
                    "content_type": library_gif.content_type,
                    "url": gif_library_media_url(settings, library_gif.id),
                    "thumbnail_url": gif_library_media_url(settings, library_gif.id),
                    "width": body.width or library_gif.width or 500,
                    "height": body.height or library_gif.height or 500,
                }
            )
        else:
            upload_id = (body.upload_token or "").removeprefix("upload:")
            upload = session.get(MediaUpload, upload_id) if upload_id else None
            if upload is None or is_expired(upload.expires_at):
                raise HTTPException(
                    status_code=422,
                    detail="Hãy chọn tệp GIF hợp lệ trước khi gửi",
                )
            if principal.kind == "user" and upload.created_by_id != principal.id:
                raise HTTPException(
                    status_code=403,
                    detail="Không được dùng upload của người khác",
                )
            if principal.kind != "user" and upload.created_by_id is not None:
                raise HTTPException(
                    status_code=403,
                    detail="Upload không thuộc API key này",
                )
            if infer_media_kind(upload.original_name, upload.content_type) != "gif":
                raise HTTPException(
                    status_code=422,
                    detail="Tệp được chọn không phải GIF",
                )
            if body.save_to_library:
                library_gif, was_created = _persist_gif_upload(
                    session,
                    upload=upload,
                    settings=settings,
                    created_by_id=principal.id if principal.kind == "user" else None,
                    width=body.width,
                    height=body.height,
                )
                if was_created:
                    created_library_gif = library_gif
                media.update(
                    {
                        "library_gif_id": library_gif.id,
                        "filename": library_gif.original_name,
                        "size": library_gif.size_bytes,
                        "content_type": library_gif.content_type,
                        "url": gif_library_media_url(settings, library_gif.id),
                        "thumbnail_url": gif_library_media_url(settings, library_gif.id),
                        "width": body.width or library_gif.width or 500,
                        "height": body.height or library_gif.height or 500,
                    }
                )
            else:
                media.update(
                    {
                        "upload_token": body.upload_token,
                        "filename": safe_upload_name(upload.original_name),
                        "size": upload.size_bytes,
                        "content_type": upload.content_type,
                        "url": outbound_media_url(settings, upload.id),
                        "thumbnail_url": body.thumbnail_url
                        or outbound_media_url(settings, upload.id),
                        "width": body.width or 500,
                        "height": body.height or 500,
                    }
                )
    elif body.kind == "link":
        parsed = urlsplit(body.url or "")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=422, detail="Liên kết phải là URL http(s) hợp lệ")
        media.update(
            {
                "url": body.url,
                "title": body.title or parsed.netloc,
                "description": body.description,
                "thumbnail_url": body.thumbnail_url,
            }
        )
    elif body.kind == "contact":
        if not body.contact_user_id:
            raise HTTPException(status_code=422, detail="Hãy chọn một người trong danh bạ")
        media.update(
            {
                "contact_user_id": body.contact_user_id,
                "contact_name": body.contact_name,
                "contact_qr_url": body.contact_qr_url or "",
                "contact_phone": body.contact_phone,
            }
        )

    context = {"kind": body.kind, "media": media}
    payload = {
        "thread_id": body.thread_id,
        "thread_type": body.thread_type,
        "kind": body.kind,
        "media": media,
        "text": body.text,
        "_chat_context": context,
    }
    job = enqueue_job(
        session,
        account_id=account.id,
        action="__chat_rich_media__",
        payload=payload,
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=45,
        max_attempts=1,
    )
    if created_library_gif is not None:
        write_audit(
            session,
            principal=principal,
            action="chat.gif_library.create",
            resource_type="gif_library_item",
            resource_id=created_library_gif.id,
            details={
                "name": created_library_gif.original_name,
                "size_bytes": created_library_gif.size_bytes,
                "sha256": created_library_gif.sha256,
            },
            request=request,
        )
    write_audit(
        session,
        principal=principal,
        action="chat.rich_media.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={
            "account_id": account.id,
            "thread_id": body.thread_id,
            "thread_type": body.thread_type,
            "kind": body.kind,
            "gif_library_id": media.get("library_gif_id"),
            "save_to_library": body.save_to_library if body.kind == "gif" else None,
        },
        request=request,
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "message": outgoing_message(job),
    }


@router.get("/chat/reactions/catalog")
def get_chat_reaction_catalog(
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> list[dict]:
    return reaction_catalog(session, include_disabled=True)


@router.put("/chat/reactions/catalog")
def update_chat_reaction_catalog(
    body: ReactionCatalogUpdate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("accounts:write")),
    _: None = Depends(verify_csrf),
) -> list[dict]:
    try:
        items = save_reaction_catalog(
            session,
            [item.model_dump() for item in body.items],
            updated_by_id=principal.id if principal.kind == "user" else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    write_audit(
        session,
        principal=principal,
        action="chat.reaction_catalog.update",
        resource_type="setting",
        resource_id="chat.reaction_catalog",
        details={
            "count": len(items),
            "custom_count": sum(not item["locked"] for item in items),
        },
        request=request,
    )
    return items


@router.post("/chat/reactions", status_code=status.HTTP_202_ACCEPTED)
def react_to_chat_message(
    body: ChatReactionCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = session.get(ZaloAccount, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    source = _chat_source_event(
        session,
        account_id=account.id,
        event_id=body.event_id,
    )
    reaction = reaction_by_id(session, body.reaction_id)
    if reaction is None:
        raise HTTPException(status_code=404, detail="Reaction không tồn tại hoặc đã bị tắt")
    reference = message_reference(source.payload or {})
    target_message_id = reference.get("msg_id") or reference.get("cli_msg_id")
    if not target_message_id:
        raise HTTPException(
            status_code=409,
            detail="Tin nhắn này thiếu ID để thả reaction",
        )
    if reaction["mode"] == "local":
        local_event = Event(
            account_id=account.id,
            event_type="chat.reaction.local",
            thread_id=source.thread_id,
            payload={
                "target_event_id": source.id,
                "target_message_id": target_message_id,
                "reaction_id": reaction["id"],
                "glyph": reaction["glyph"],
                "label": reaction["label"],
                "actor_id": principal.id,
                "action": "remove" if body.remove else "add",
            },
        )
        session.add(local_event)
        session.commit()
        session.refresh(local_event)
        write_audit(
            session,
            principal=principal,
            action="chat.reaction.local.remove" if body.remove else "chat.reaction.local.add",
            resource_type="event",
            resource_id=str(source.id),
            details={"reaction_id": reaction["id"], "thread_id": source.thread_id},
            request=request,
        )
        return {
            "status": "succeeded",
            "event_id": local_event.id,
            "job_id": None,
            "remove": body.remove,
            "reaction": reaction,
        }
    if not account.enabled or account.status == "error":
        raise HTTPException(
            status_code=409,
            detail="Phiên Zalo đang lỗi hoặc đã bị tắt",
        )
    job = enqueue_job(
        session,
        account_id=account.id,
        action="__chat_reaction__",
        payload={
            "messageObject": (source.payload or {}).get("message_object") or {},
            "reactionIcon": "" if body.remove else reaction["zalo_icon"],
            "reactionType": -1 if body.remove else reaction["r_type"],
            "source": reaction["source"],
            "thread_id": source.thread_id,
            "thread_type": (source.payload or {}).get("thread_type") or "user",
            "_chat_context": {
                "target_event_id": source.id,
                "target_message_id": target_message_id,
                "reaction_id": reaction["id"],
                "glyph": reaction["glyph"],
                "label": reaction["label"],
                "actor_id": principal.id,
                "remove": body.remove,
                "fallback": "none" if body.remove else reaction["fallback"],
            },
        },
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=35,
        max_attempts=1 if reaction["mode"] == "experimental" else 2,
    )
    write_audit(
        session,
        principal=principal,
        action="chat.reaction.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={
            "reaction_id": reaction["id"],
            "mode": reaction["mode"],
            "remove": body.remove,
            "thread_id": source.thread_id,
        },
        request=request,
    )
    return {
        "status": job.status,
        "job_id": job.id,
        "remove": body.remove,
        "reaction": reaction,
    }


@router.post("/chat/forward", status_code=status.HTTP_202_ACCEPTED)
def forward_chat_message(
    body: ChatForwardCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = session.get(ZaloAccount, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if not account.enabled or account.status == "error":
        raise HTTPException(status_code=409, detail="Phiên Zalo đang lỗi hoặc đã bị tắt")
    source = _chat_source_event(
        session,
        account_id=account.id,
        event_id=body.event_id,
    )
    kind, media = message_media(source.payload or {})
    source_text = message_text(source.payload or {})
    if kind == "poll":
        raise HTTPException(
            status_code=409,
            detail="ZLAPI hiện chưa hỗ trợ chia sẻ lại bình chọn",
        )
    source_message = incoming_message(source, account)
    jobs = []
    for target in body.targets:
        job = enqueue_job(
            session,
            account_id=account.id,
            action="__chat_forward__",
            payload={
                "thread_id": target.thread_id,
                "thread_type": target.thread_type,
                "text": source_text,
                "kind": kind,
                "media": media,
                "source_message": (source.payload or {}).get("message"),
                "_chat_context": {
                    "forwarded": True,
                    "source_event_id": source.id,
                    "source_message_id": source_message["id"],
                    "kind": kind,
                    "media": media,
                    "target_name": target.name,
                },
            },
            created_by_id=principal.id if principal.kind == "user" else None,
            priority=55,
            max_attempts=2,
        )
        jobs.append(job)
    write_audit(
        session,
        principal=principal,
        action="chat.forward.enqueue",
        resource_type="event",
        resource_id=str(source.id),
        details={
            "target_count": len(jobs),
            "kind": kind,
            "job_ids": [job.id for job in jobs],
        },
        request=request,
    )
    return {
        "status": "queued",
        "job_ids": [job.id for job in jobs],
        "target_count": len(jobs),
    }


@router.post("/chat/undo", status_code=status.HTTP_202_ACCEPTED)
def undo_chat_message(
    body: ChatUndoCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("jobs:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    account = session.get(ZaloAccount, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    source = _chat_source_event(
        session,
        account_id=account.id,
        event_id=body.event_id,
    )
    source_message = incoming_message(source, account)
    if not source_message["mine"]:
        raise HTTPException(
            status_code=403,
            detail="Chỉ được thu hồi tin nhắn do tài khoản hiện tại gửi",
        )
    reference = message_reference(source.payload or {})
    if not reference.get("msg_id") or not reference.get("cli_msg_id"):
        raise HTTPException(status_code=409, detail="Tin nhắn thiếu ID để thu hồi")
    job = enqueue_job(
        session,
        account_id=account.id,
        action="undoMessage",
        payload={
            "msgId": reference["msg_id"],
            "cliMsgId": reference["cli_msg_id"],
            "thread_id": source.thread_id,
            "thread_type": (source.payload or {}).get("thread_type") or "user",
        },
        created_by_id=principal.id if principal.kind == "user" else None,
        priority=20,
        max_attempts=1,
    )
    write_audit(
        session,
        principal=principal,
        action="chat.undo.enqueue",
        resource_type="job",
        resource_id=job.id,
        details={"source_event_id": source.id, "thread_id": source.thread_id},
        request=request,
    )
    return {"status": job.status, "job_id": job.id}


@router.get("/events")
def list_events(
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
    cursor: int = Query(default=0, ge=0),
    account_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    statement = select(Event).where(Event.id > cursor).order_by(Event.id).limit(limit)
    if account_id:
        statement = statement.where(Event.account_id == account_id)
    return [
        {
            "id": event.id,
            "account_id": event.account_id,
            "event_type": event.event_type,
            "thread_id": event.thread_id,
            "payload": event.payload,
            "created_at": event.created_at,
            "archived_at": event.archived_at,
        }
        for event in session.scalars(statement).all()
    ]


@router.get("/events/stream")
async def event_stream(
    request: Request,
    _: Principal = Depends(require_short_lived_scope("read")),
) -> StreamingResponse:
    async def generate():
        broker = request.app.state.event_broker
        queue = broker.add_subscriber()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"id: {event['id']}\nevent: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            broker.remove_subscriber(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_media(
    request: Request,
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("uploads:create")),
    _: None = Depends(verify_csrf),
) -> dict:
    max_bytes = request.app.state.settings.max_upload_mb * 1024 * 1024
    try:
        original_name = validate_upload_name(file.filename or "upload")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    suffix = Path(original_name).suffix[:16]
    upload_id = secrets.token_hex(16)
    target = request.app.state.settings.upload_dir / f"{upload_id}{suffix}"
    size = 0
    try:
        with target.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail="Upload is too large")
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if size <= 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Tệp tải lên đang trống")
    upload = MediaUpload(
        id=upload_id,
        original_name=original_name,
        stored_path=str(target),
        size_bytes=size,
        content_type=file.content_type,
        created_by_id=principal.id if principal.kind == "user" else None,
        expires_at=utcnow() + timedelta(hours=24),
    )
    session.add(upload)
    session.commit()
    write_audit(
        session,
        principal=principal,
        action="upload.create",
        resource_type="media_upload",
        resource_id=upload.id,
        details={"name": upload.original_name, "size_bytes": size},
        request=request,
    )
    return {
        "id": upload.id,
        "token": f"upload:{upload.id}",
        "name": upload.original_name,
        "size_bytes": size,
        "content_type": upload.content_type,
        "kind": infer_media_kind(upload.original_name, upload.content_type),
        "preview_url": outbound_media_url(request.app.state.settings, upload.id),
        "expires_at": upload.expires_at,
    }


@router.get("/users")
def list_users(
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("users:read")),
) -> list[dict]:
    return [user_payload(user) for user in session.scalars(select(User).order_by(User.username)).all()]


@router.put("/me/password")
def change_own_password(
    body: PasswordChange,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    _: None = Depends(verify_csrf),
) -> dict:
    if principal.kind != "user":
        raise HTTPException(status_code=403, detail="API key không thể đổi mật khẩu người dùng")
    user = session.get(User, principal.id)
    if user is None or not user.active:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Phiên đăng nhập không còn hợp lệ")
    if not verify_secret(user.password_hash, body.current_password):
        raise HTTPException(status_code=422, detail="Mật khẩu hiện tại không đúng")
    if verify_secret(user.password_hash, body.new_password):
        raise HTTPException(
            status_code=422,
            detail="Mật khẩu mới phải khác mật khẩu hiện tại",
        )

    user.password_hash = hash_secret(body.new_password)
    user.failed_logins = 0
    user.locked_until = None
    session.commit()
    write_audit(
        session,
        principal=principal,
        action="auth.password_change",
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username},
        request=request,
    )
    request.session.clear()
    return {"changed": True, "reauthenticate": True}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("users:manage")),
    _: None = Depends(verify_csrf),
) -> dict:
    if body.role == "super_admin" and principal.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Super Admin can grant Super Admin")
    user = User(
        username=body.username.lower(),
        display_name=body.display_name,
        password_hash=hash_secret(body.password),
        role=body.role,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    write_audit(
        session,
        principal=principal,
        action="user.create",
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username, "role": user.role},
        request=request,
    )
    return user_payload(user)


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    body: UserUpdate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("users:manage")),
    _: None = Depends(verify_csrf),
) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("role") == "super_admin" and principal.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Super Admin can grant Super Admin")
    if user.role == "super_admin" and principal.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Super Admin can modify Super Admin")
    if "password" in changes:
        user.password_hash = hash_secret(changes.pop("password"))
    for key, value in changes.items():
        setattr(user, key, value)
    session.commit()
    write_audit(
        session,
        principal=principal,
        action="user.update",
        resource_type="user",
        resource_id=user.id,
        details={"fields": sorted(body.model_fields_set)},
        request=request,
    )
    return user_payload(user)


@router.get("/api-keys")
def list_api_keys(
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("keys:manage")),
) -> list[dict]:
    return [
        {
            "id": key.id,
            "name": key.name,
            "prefix": key.prefix,
            "scopes": key.scopes,
            "expires_at": key.expires_at,
            "revoked_at": key.revoked_at,
            "last_used_at": key.last_used_at,
            "created_at": key.created_at,
        }
        for key in session.scalars(select(ApiKey).order_by(desc(ApiKey.created_at))).all()
    ]


@router.post("/api-keys", status_code=status.HTTP_201_CREATED)
def create_api_key(
    body: ApiKeyCreate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("keys:manage")),
    _: None = Depends(verify_csrf),
) -> dict:
    allowed = set().union(*ROLE_SCOPES.values())
    unknown = set(body.scopes) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown scopes: {sorted(unknown)}")
    if "*" in body.scopes and principal.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only Super Admin can issue wildcard keys")
    prefix, complete, encoded = generate_api_key()
    key = ApiKey(
        name=body.name,
        prefix=prefix,
        secret_hash=encoded,
        scopes=sorted(set(body.scopes)),
        expires_at=body.expires_at,
        created_by_id=principal.id if principal.kind == "user" else None,
    )
    session.add(key)
    session.commit()
    write_audit(
        session,
        principal=principal,
        action="api_key.create",
        resource_type="api_key",
        resource_id=key.id,
        details={"name": key.name, "scopes": key.scopes},
        request=request,
    )
    return {
        "id": key.id,
        "name": key.name,
        "prefix": key.prefix,
        "scopes": key.scopes,
        "api_key": complete,
        "message": "Copy this key now; it will never be shown again.",
    }


@router.post("/api-keys/{key_id}/revoke")
def revoke_api_key(
    key_id: str,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("keys:manage")),
    _: None = Depends(verify_csrf),
) -> dict:
    key = session.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    key.revoked_at = utcnow()
    session.commit()
    write_audit(
        session,
        principal=principal,
        action="api_key.revoke",
        resource_type="api_key",
        resource_id=key.id,
        request=request,
    )
    return {"id": key.id, "revoked": True}


@router.get("/audit")
def list_audit(
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    entries = session.scalars(select(AuditLog).order_by(desc(AuditLog.id)).limit(limit)).all()
    return [
        {
            "id": entry.id,
            "actor_type": entry.actor_type,
            "actor_id": entry.actor_id,
            "action": entry.action,
            "resource_type": entry.resource_type,
            "resource_id": entry.resource_id,
            "details": entry.details,
            "ip_address": entry.ip_address,
            "created_at": entry.created_at,
        }
        for entry in entries
    ]


@router.get("/settings")
def list_settings(
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> dict:
    return {item.key: item.value for item in session.scalars(select(AppSetting)).all()}


@router.get("/archives")
def list_archives(
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> list[dict]:
    return [
        {
            "id": archive.id,
            "record_count": archive.record_count,
            "range_start": archive.range_start,
            "range_end": archive.range_end,
            "size_bytes": archive.size_bytes,
            "sha256": archive.sha256,
            "created_at": archive.created_at,
        }
        for archive in session.scalars(select(Archive).order_by(desc(Archive.created_at))).all()
    ]


@router.get("/archives/{archive_id}/content")
def read_archive(
    archive_id: str,
    request: Request,
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> StreamingResponse:
    archive = session.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(status_code=404, detail="Archive not found")
    path = Path(archive.path)
    try:
        path.resolve().relative_to(request.app.state.settings.archive_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Archive path is invalid") from exc
    encrypted = path.read_bytes()
    if hashlib.sha256(encrypted).hexdigest() != archive.sha256:
        raise HTTPException(status_code=409, detail="Archive checksum mismatch")
    compressed = request.app.state.secret_box.decrypt_bytes(encrypted)
    content = zstandard.ZstdDecompressor().decompress(compressed)
    return StreamingResponse(
        iter([content]),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="archive-{archive.id}.jsonl"'},
    )


@router.put("/settings/{key}")
def update_setting(
    key: str,
    body: SettingUpdate,
    request: Request,
    session: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("accounts:write")),
    _: None = Depends(verify_csrf),
) -> dict:
    if not key.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=422, detail="Invalid setting key")
    setting = session.get(AppSetting, key) or AppSetting(key=key)
    setting.value = body.value
    setting.updated_by_id = principal.id if principal.kind == "user" else None
    session.add(setting)
    session.commit()
    write_audit(
        session,
        principal=principal,
        action="setting.update",
        resource_type="setting",
        resource_id=key,
        request=request,
    )
    return {"key": key, "value": setting.value}


@router.get("/health")
def system_health(
    request: Request,
    session: Session = Depends(get_db),
    _: Principal = Depends(require_scope("read")),
) -> dict:
    database_bytes = 0
    if request.app.state.settings.is_postgres:
        database_bytes = int(session.scalar(select(func.pg_database_size(func.current_database()))) or 0)
    else:
        database_path = request.app.state.settings.normalized_database_url.removeprefix("sqlite:///")
        if database_path and database_path != ":memory:":
            path = Path(database_path)
            database_bytes = path.stat().st_size if path.exists() else 0
    cap = request.app.state.settings.database_cap_bytes
    return {
        "status": "ok",
        "database": {
            "dialect": session.bind.dialect.name,
            "size_bytes": database_bytes,
            "cap_bytes": cap,
            "usage_percent": round(database_bytes / cap * 100, 2) if cap else None,
        },
        "accounts": {
            status_name: count
            for status_name, count in session.execute(
                select(ZaloAccount.status, func.count()).group_by(ZaloAccount.status)
            ).all()
        },
        "jobs": {
            status_name: count
            for status_name, count in session.execute(
                select(Job.status, func.count()).group_by(Job.status)
            ).all()
        },
        "event_cursor": int(session.scalar(select(func.max(Event.id))) or 0),
        "archives": int(session.scalar(select(func.count(Archive.id))) or 0),
    }
