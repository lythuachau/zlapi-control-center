from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from .models import AppSetting

REACTION_CATALOG_KEY = "chat.reaction_catalog"
REACTION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def _native_reaction(
    reaction_id: str,
    label: str,
    glyph: str,
    zalo_icon: str,
    r_type: int,
    *,
    quick: bool = False,
) -> dict[str, Any]:
    return {
        "id": reaction_id,
        "label": label,
        "glyph": glyph,
        "zalo_icon": zalo_icon,
        "r_type": r_type,
        "source": 6,
        "mode": "native",
        "quick": quick,
        "enabled": True,
        "verified": True,
        "fallback": "none",
        "locked": True,
    }


# Complete native reaction map exposed by the current Zalo Web-compatible clients.
# The empty NONE reaction is intentionally excluded because removal is a separate action.
DEFAULT_REACTIONS: list[dict[str, Any]] = [
    _native_reaction("like", "Thích", "👍", "/-strong", 3, quick=True),
    _native_reaction("heart", "Yêu thích", "❤️", "/-heart", 5, quick=True),
    _native_reaction("haha", "Haha", "😆", ":>", 0, quick=True),
    _native_reaction("wow", "Ngạc nhiên", "😮", ":o", 32, quick=True),
    _native_reaction("cry", "Buồn", "😢", ":-((", 2, quick=True),
    _native_reaction("angry", "Phẫn nộ", "😡", ":-h", 20, quick=True),
    _native_reaction("kiss", "Hôn", "😘", ":-*", 8),
    _native_reaction("tears-of-joy", "Cười ra nước mắt", "😂", ":')", 7),
    _native_reaction("shit", "Ôi không", "💩", "/-shit", 66),
    _native_reaction("rose", "Hoa hồng", "🌹", "/-rose", 120),
    _native_reaction("broken-heart", "Tan vỡ", "💔", "/-break", 65),
    _native_reaction("dislike", "Không thích", "👎", "/-weak", 4),
    _native_reaction("love", "Thương mến", "🥰", ";xx", 29),
    _native_reaction("confused", "Bối rối", "😕", ";-/", 51),
    _native_reaction("wink", "Nháy mắt", "😉", ";-)", 45),
    _native_reaction("fade", "Biến mất", "🫥", "/-fade", 121),
    _native_reaction("sun", "Tỏa sáng", "☀️", "/-li", 67),
    _native_reaction("birthday", "Sinh nhật", "🎂", "/-bd", 126),
    _native_reaction("bomb", "Bùng nổ", "💣", "/-bome", 127),
    _native_reaction("ok", "OK", "👌", "/-ok", 68),
    _native_reaction("peace", "Chiến thắng", "✌️", "/-v", 69),
    _native_reaction("thanks", "Cảm ơn", "🙏", "/-thanks", 70),
    _native_reaction("punch", "Cú đấm", "👊", "/-punch", 71),
    _native_reaction("share", "Chia sẻ", "↗️", "/-share", 72),
    _native_reaction("pray", "Cúi chào", "🙇", "_()_", 73),
    _native_reaction("no", "Không", "🙅", "/-no", 131),
    _native_reaction("bad", "Không ổn", "😬", "/-bad", 132),
    _native_reaction("love-you", "Yêu bạn", "🤟", "/-loveu", 133),
    _native_reaction("sad", "Buồn nhẹ", "😔", "--b", 1),
    _native_reaction("very-sad", "Rất buồn", "😭", ":((", 16),
    _native_reaction("cool", "Ngầu", "😎", "x-)", 21),
    _native_reaction("nerd", "Mọt sách", "🤓", "8-)", 22),
    _native_reaction("big-smile", "Cười tươi", "😁", ";-d", 23),
    _native_reaction("sunglasses", "Kính râm", "🕶️", "b-)", 26),
    _native_reaction("neutral", "Bình thường", "😐", ":--|", 30),
    _native_reaction("sad-face", "Mặt buồn", "☹️", "p-(", 35),
    _native_reaction("bye", "Tạm biệt", "👋", ":-bye", 36),
    _native_reaction("sleepy", "Buồn ngủ", "😴", "|-)", 38),
    _native_reaction("wipe", "Lau nước mắt", "🥹", ":wipe", 39),
    _native_reaction("dig", "Đào", "⛏️", ":-dig", 42),
    _native_reaction("anguish", "Đau khổ", "😖", "&-(", 44),
    _native_reaction("handclap", "Vỗ tay", "👏", ":handclap", 46),
    _native_reaction("angry-face", "Tức giận", "🤬", ">-|", 47),
    _native_reaction("f-chair", "Ghế trước", "🪑F", ":-f", 48),
    _native_reaction("l-chair", "Ghế trái", "🪑L", ":-l", 49),
    _native_reaction("r-chair", "Ghế phải", "🪑R", ":-r", 50),
    _native_reaction("silent", "Im lặng", "🤐", ";-x", 52),
    _native_reaction("surprise", "Bất ngờ", "😲", ":-o", 53),
    _native_reaction("embarrassed", "Ngượng ngùng", "😳", ";-s", 54),
    _native_reaction("afraid", "Sợ hãi", "😨", ";-a", 60),
    _native_reaction("sad-2", "Thất vọng", "🙁", ":-<", 61),
    _native_reaction("big-laugh", "Cười lớn", "🤣", ":))", 62),
    _native_reaction("rich", "Giàu có", "🤑", "$-)", 63),
    _native_reaction("beer", "Chúc mừng", "🍻", "/-beer", 99),
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


def _merge_with_native_defaults(raw_items: list[Any] | None) -> list[dict[str, Any]]:
    saved_by_id: dict[str, dict[str, Any]] = {}
    for raw in raw_items or []:
        item = _normalize_item(raw)
        if item is not None:
            saved_by_id[item["id"]] = item

    merged: list[dict[str, Any]] = []
    default_ids: set[str] = set()
    for raw_default in DEFAULT_REACTIONS:
        default = deepcopy(raw_default)
        default_ids.add(default["id"])
        saved = saved_by_id.get(default["id"])
        if saved is not None:
            default["enabled"] = saved["enabled"]
            default["quick"] = saved["quick"] and saved["enabled"]
        merged.append(default)

    for item in saved_by_id.values():
        if item["id"] not in default_ids:
            merged.append(item)
    return merged


def reaction_catalog(session: Session, *, include_disabled: bool = False) -> list[dict[str, Any]]:
    setting = session.get(AppSetting, REACTION_CATALOG_KEY)
    raw_items = setting.value if setting and isinstance(setting.value, list) else None
    items = _merge_with_native_defaults(raw_items)
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
    for raw in items:
        item = _normalize_item(raw)
        if item is None:
            raise ValueError("Danh mục reaction chứa mục không hợp lệ")
        if item["id"] in seen:
            raise ValueError(f"Reaction ID bị trùng: {item['id']}")
        seen.add(item["id"])
        normalized.append(item)
    if not normalized:
        raise ValueError("Danh mục reaction không được để trống")
    normalized = _merge_with_native_defaults(normalized)
    if len(normalized) > 80:
        raise ValueError("Thư viện chỉ chứa tối đa 80 reaction")
    quick_count = sum(item["quick"] and item["enabled"] for item in normalized)
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
