from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ZaloContactProfile, utcnow

IDENTITY_KEYS = (
    "userId",
    "user_id",
    "uid",
    "id",
    "userID",
    "zaloId",
    "globalId",
)
NAME_KEYS = (
    "displayName",
    "dName",
    "zaloName",
    "fullName",
    "name",
    "username",
)
AVATAR_KEYS = (
    "avatar",
    "avatar_25",
    "avatarUrl",
    "avt",
    "fullAvt",
    "avatar_url",
)


def profile_text(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def profile_records(value: Any) -> list[tuple[str | None, dict]]:
    if isinstance(value, (list, tuple)):
        records: list[tuple[str | None, dict]] = []
        for item in value:
            records.extend(profile_records(item))
        return records
    if not isinstance(value, dict):
        return []
    for key in ("profiles", "friends", "users", "items", "data", "results"):
        nested = value.get(key)
        if isinstance(nested, (list, tuple, dict)):
            records = profile_records(nested)
            if records:
                return records
    if any(profile_text(value.get(key)) for key in IDENTITY_KEYS):
        return [(None, value)]
    records = []
    for key, item in value.items():
        if not isinstance(item, dict):
            continue
        if (
            any(profile_text(item.get(identity)) for identity in IDENTITY_KEYS)
            or any(profile_text(item.get(name_key)) for name_key in NAME_KEYS)
            or any(profile_text(item.get(avatar_key)) for avatar_key in AVATAR_KEYS)
        ):
            records.append((str(key), item))
        else:
            records.extend(profile_records(item))
    return records


def safe_avatar_url(value: Any) -> str | None:
    candidate = profile_text(value)
    if not candidate:
        return None
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def profile_initials(name: str) -> str:
    words = [part for part in name.replace("-", " ").split() if part]
    if not words:
        return "ZL"
    if len(words) == 1:
        return words[0][:2].upper()
    return f"{words[0][0]}{words[-1][0]}".upper()


def normalize_profiles(result: Any) -> list[dict]:
    profiles: list[dict] = []
    seen: set[str] = set()
    for fallback_id, item in profile_records(result):
        user_id = next(
            (
                profile_text(item.get(key))
                for key in IDENTITY_KEYS
                if profile_text(item.get(key))
            ),
            None,
        ) or fallback_id
        if not user_id or user_id in seen:
            continue
        name = next(
            (
                profile_text(item.get(key))
                for key in NAME_KEYS
                if profile_text(item.get(key))
            ),
            None,
        ) or f"Zalo {user_id[-6:]}"
        avatar_url = next(
            (
                safe_avatar_url(item.get(key))
                for key in AVATAR_KEYS
                if safe_avatar_url(item.get(key))
            ),
            None,
        )
        online = item.get("online")
        if online is None:
            online = item.get("isOnline")
        if online is None:
            online = item.get("isActive")
        if online is None:
            account_status = item.get("accountStatus")
            online = bool(account_status) if account_status is not None else None
        profiles.append(
            {
                "user_id": user_id,
                "name": name,
                "initials": profile_initials(name),
                "avatar_url": avatar_url,
                "online": bool(online) if online is not None else None,
                "phone": profile_text(item.get("phoneNumber") or item.get("phone")),
            }
        )
        seen.add(user_id)
    profiles.sort(key=lambda item: item["name"].casefold())
    return profiles


def cache_profiles(
    session: Session,
    *,
    account_id: str,
    result: Any,
    source: str,
    requested_user_ids: list[str] | None = None,
) -> list[dict]:
    normalized = normalize_profiles(result)
    by_id = {item["user_id"]: item for item in normalized}
    requested = {
        str(user_id).strip()
        for user_id in (requested_user_ids or [])
        if str(user_id).strip()
    }
    for user_id in requested - set(by_id):
        fallback_name = f"Zalo {user_id[-6:]}"
        by_id[user_id] = {
            "user_id": user_id,
            "name": fallback_name,
            "initials": profile_initials(fallback_name),
            "avatar_url": None,
            "online": None,
            "phone": None,
        }

    existing = {
        item.user_id: item
        for item in session.scalars(
            select(ZaloContactProfile).where(
                ZaloContactProfile.account_id == account_id,
                ZaloContactProfile.user_id.in_(tuple(by_id)),
            )
        ).all()
    } if by_id else {}
    synced_at = utcnow()
    for user_id, item in by_id.items():
        row = existing.get(user_id)
        if row is None:
            row = ZaloContactProfile(
                account_id=account_id,
                user_id=user_id,
                name=item["name"],
            )
            session.add(row)
        is_fallback = item["name"] == f"Zalo {user_id[-6:]}"
        if not is_fallback or not row.name:
            row.name = item["name"]
        if item["avatar_url"]:
            row.avatar_url = item["avatar_url"]
        if item["phone"]:
            row.phone = item["phone"]
        if item["online"] is not None:
            row.online = item["online"]
        row.source = source
        row.synced_at = synced_at
    session.flush()
    return sorted(by_id.values(), key=lambda item: item["name"].casefold())


def cached_profiles(
    session: Session,
    *,
    account_id: str,
    user_ids: set[str] | None = None,
) -> dict[str, ZaloContactProfile]:
    statement = select(ZaloContactProfile).where(
        ZaloContactProfile.account_id == account_id
    )
    if user_ids is not None:
        if not user_ids:
            return {}
        statement = statement.where(
            ZaloContactProfile.user_id.in_(tuple(user_ids))
        )
    return {
        item.user_id: item
        for item in session.scalars(statement).all()
    }
