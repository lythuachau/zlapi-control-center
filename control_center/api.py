from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any

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
    Job,
    MediaUpload,
    User,
    ZaloAccount,
    utcnow,
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
    ChatGroupInviteCreate,
    ChatGroupRenameCreate,
    ChatMediaCreate,
    ChatMessageCreate,
    ChatReactionCreate,
    ChatReadStateUpdate,
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
    verify_secret,
)

router = APIRouter(prefix="/api/v1")


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
    if isinstance(value, list):
        return [
            (None, item)
            for item in value
            if isinstance(item, dict)
        ]
    if not isinstance(value, dict):
        return []
    for key in ("friends", "users", "items", "data", "results"):
        nested = value.get(key)
        if isinstance(nested, (list, dict)):
            records = _contact_records(nested)
            if records:
                return records
    identity_keys = {
        "userId",
        "user_id",
        "uid",
        "id",
        "userID",
        "zaloId",
    }
    if identity_keys.intersection(value):
        return [(None, value)]
    return [
        (str(key), item)
        for key, item in value.items()
        if isinstance(item, dict)
    ]


def _normalize_contacts(result: Any) -> list[dict]:
    contacts: list[dict] = []
    seen: set[str] = set()
    for fallback_id, item in _contact_records(result):
        user_id = next(
            (
                _contact_text(item.get(key))
                for key in ("userId", "user_id", "uid", "id", "userID", "zaloId")
                if _contact_text(item.get(key))
            ),
            None,
        ) or fallback_id
        if not user_id or user_id in seen:
            continue
        name = next(
            (
                _contact_text(item.get(key))
                for key in (
                    "displayName",
                    "dName",
                    "zaloName",
                    "fullName",
                    "name",
                    "username",
                )
                if _contact_text(item.get(key))
            ),
            None,
        ) or f"Zalo {user_id[-6:]}"
        avatar_url = next(
            (
                _contact_text(item.get(key))
                for key in ("avatar", "avatarUrl", "avt", "fullAvt", "avatar_url")
                if _contact_text(item.get(key))
            ),
            None,
        )
        if avatar_url and not avatar_url.startswith(("https://", "http://")):
            avatar_url = None
        online = item.get("online")
        if online is None:
            online = item.get("isOnline")
        if online is None:
            online = item.get("isActive")
        contacts.append(
            {
                "user_id": user_id,
                "name": name,
                "initials": "".join(
                    part[0] for part in name.replace("-", " ").split() if part
                )[:2].upper()
                or "ZL",
                "avatar_url": avatar_url,
                "online": bool(online) if online is not None else None,
                "phone": _contact_text(item.get("phoneNumber") or item.get("phone")),
            }
        )
        seen.add(user_id)
    contacts.sort(key=lambda item: item["name"].casefold())
    return contacts


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
    return {
        "contacts": _normalize_contacts(latest_success.result if latest_success else None),
        "last_sync_at": latest_success.finished_at if latest_success else None,
        "job": _contacts_job_payload(latest_job),
    }


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
