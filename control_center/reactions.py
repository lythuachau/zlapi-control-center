from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from .models import AppSetting

REACTION_CATALOG_KEY = "chat.reaction_catalog"
REACTION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


DEFAULT_REACTIONS: list[dict[str, Any]] = [
    {
        "id": "like",
        "label": "Thích",
        "glyph": "👍",
        "zalo_icon": "/-strong",
        "r_type": 3,
        "source": 6,
        "mode": "native",
        "quick": True,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    },
    {
        "id": "heart",
        "label": "Yêu thích",
        "glyph": "❤️",
        "zalo_icon": "/-heart",
        "r_type": 5,
        "source": 6,
        "mode": "native",
        "quick": True,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    },
    {
        "id": "haha",
        "label": "Haha",
        "glyph": "😆",
        "zalo_icon": ":>",
        "r_type": 0,
        "source": 6,
        "mode": "native",
        "quick": True,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    },
    {
        "id": "wow",
        "label": "Ngạc nhiên",
        "glyph": "😮",
        "zalo_icon": ":o",
        "r_type": 32,
        "source": 6,
        "mode": "native",
        "quick": True,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    },
    {
        "id": "cry",
        "label": "Buồn",
        "glyph": "😢",
        "zalo_icon": ":-((",
        "r_type": 2,
        "source": 6,
        "mode": "native",
        "quick": True,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    },
    {
        "id": "angry",
        "label": "Phẫn nộ",
        "glyph": "😡",
        "zalo_icon": ":-h",
        "r_type": 20,
        "source": 6,
        "mode": "native",
        "quick": True,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    },
    {
        "id": "thanks",
        "label": "Cảm ơn",
        "glyph": "🙏",
        "zalo_icon": "/-thanks",
        "r_type": 70,
        "source": 6,
        "mode": "native",
        "quick": False,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    },
    {
        "id": "ok",
        "label": "OK",
        "glyph": "👌",
        "zalo_icon": "/-ok",
        "r_type": 68,
        "source": 6,
        "mode": "native",
        "quick": False,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    },
    {
        "id": "rose",
        "label": "Hoa hồng",
        "glyph": "🌹",
        "zalo_icon": "/-rose",
        "r_type": 120,
        "source": 6,
        "mode": "native",
        "quick": False,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    },
    {
        "id": "beer",
        "label": "Chúc mừng",
        "glyph": "🍻",
        "zalo_icon": "/-beer",
        "r_type": 99,
        "source": 6,
        "mode": "native",
        "quick": False,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    },
    {
        "id": "birthday",
        "label": "Sinh nhật",
        "glyph": "🎂",
        "zalo_icon": "/-bd",
        "r_type": 126,
        "source": 6,
        "mode": "native",
        "quick": False,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    },
    {
        "id": "handclap",
        "label": "Vỗ tay",
        "glyph": "👏",
        "zalo_icon": ":handclap",
        "r_type": 46,
        "source": 6,
        "mode": "native",
        "quick": False,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    },
]


def _normalize_item(raw: Any, *, locked_default: bool = False) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    reaction_id = str(raw.get("id") or "").strip().lower()
    if not REACTION_ID_PATTERN.fullmatch(reaction_id):
        return None
    mode = str(raw.get("mode") or "experimental").strip().lower()
    if mode not in {"native", "experimental", "local"}:
        return None
    fallback = str(raw.get("fallback") or "none").strip().lower()
    if fallback not in {"none", "message", "local"}:
        fallback = "none"
    glyph = str(raw.get("glyph") or "").strip()
    label = str(raw.get("label") or "").strip()
    zalo_icon = str(raw.get("zalo_icon") or glyph).strip()
    if not glyph or len(glyph) > 16 or not label or len(label) > 60:
        return None
    try:
        r_type = int(raw.get("r_type", -1))
        source = int(raw.get("source", 6))
    except (TypeError, ValueError):
        return None
    if not -1 <= r_type <= 10_000 or not 0 <= source <= 100:
        return None
    if mode != "local" and not zalo_icon:
        return None
    return {
        "id": reaction_id,
        "label": label,
        "glyph": glyph,
        "zalo_icon": zalo_icon,
        "r_type": r_type,
        "source": source,
        "mode": mode,
        "quick": bool(raw.get("quick", False)),
        "enabled": bool(raw.get("enabled", True)),
        "verified": bool(raw.get("verified", mode == "native")),
        "fallback": fallback,
        "locked": bool(raw.get("locked", locked_default)),
    }


def reaction_catalog(session: Session, *, include_disabled: bool = False) -> list[dict[str, Any]]:
    setting = session.get(AppSetting, REACTION_CATALOG_KEY)
    raw_items = setting.value if setting and isinstance(setting.value, list) else None
    items = []
    for raw in raw_items or deepcopy(DEFAULT_REACTIONS):
        item = _normalize_item(raw)
        if item is not None:
            items.append(item)
    if not items:
        items = deepcopy(DEFAULT_REACTIONS)
    if include_disabled:
        return items
    return [item for item in items if item["enabled"]]


def save_reaction_catalog(
    session: Session,
    items: list[dict[str, Any]],
    *,
    updated_by_id: str | None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    quick_count = 0
    for raw in items:
        item = _normalize_item(raw)
        if item is None:
            raise ValueError("Danh mục reaction chứa mục không hợp lệ")
        if item["id"] in seen:
            raise ValueError(f"Reaction ID bị trùng: {item['id']}")
        if item["quick"] and item["enabled"]:
            quick_count += 1
        seen.add(item["id"])
        normalized.append(item)
    if not normalized:
        raise ValueError("Danh mục reaction không được để trống")
    if quick_count > 8:
        raise ValueError("Chỉ được chọn tối đa 8 reaction nhanh")
    setting = session.get(AppSetting, REACTION_CATALOG_KEY)
    if setting is None:
        setting = AppSetting(
            key=REACTION_CATALOG_KEY,
            value=normalized,
            updated_by_id=updated_by_id,
        )
        session.add(setting)
    else:
        setting.value = normalized
        setting.updated_by_id = updated_by_id
    session.commit()
    return normalized


def reaction_by_id(session: Session, reaction_id: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in reaction_catalog(session)
            if item["id"] == reaction_id and item["enabled"]
        ),
        None,
    )


def reaction_presentation(
    session: Session,
    *,
    catalog: list[dict[str, Any]] | None = None,
    reaction_id: str | None = None,
    zalo_icon: str | None = None,
    glyph: str | None = None,
    label: str | None = None,
    mode: str | None = None,
) -> dict[str, str]:
    item = None
    for candidate in (
        catalog
        if catalog is not None
        else reaction_catalog(session, include_disabled=True)
    ):
        if reaction_id and candidate["id"] == reaction_id:
            item = candidate
            break
        if zalo_icon is not None and candidate["zalo_icon"] == zalo_icon:
            item = candidate
            break
    if item:
        return {
            "reaction_id": item["id"],
            "glyph": item["glyph"],
            "label": item["label"],
            "mode": item["mode"],
        }
    rendered = (glyph or zalo_icon or "✨").strip()
    if rendered.startswith(("/", ":", ";", "_", "-", "&", ">", "|", "$")):
        rendered = "✨"
    unknown_id = hashlib.sha256((zalo_icon or rendered).encode("utf-8")).hexdigest()[:12]
    return {
        "reaction_id": reaction_id or f"unknown-{unknown_id}",
        "glyph": rendered[:16] or "✨",
        "label": (label or "Reaction")[:60],
        "mode": mode or "experimental",
    }
