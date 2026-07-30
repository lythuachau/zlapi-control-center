from __future__ import annotations

from copy import deepcopy

from control_center.db import Database
from control_center.models import AppSetting
from control_center.reactions import (
    DEFAULT_REACTIONS,
    REACTION_CATALOG_KEY,
    reaction_catalog,
    save_reaction_catalog,
)


def test_complete_native_reaction_catalog_is_available(settings):
    database = Database(settings)
    database.create_schema()

    with database.session_factory() as session:
        catalog = reaction_catalog(session, include_disabled=True)

    assert len(catalog) == 54
    assert len({item["id"] for item in catalog}) == 54
    assert len({(item["zalo_icon"], item["r_type"]) for item in catalog}) == 54
    assert all(
        item["mode"] == "native"
        and item["verified"] is True
        and item["locked"] is True
        and item["source"] == 6
        for item in catalog
    )
    by_id = {item["id"]: item for item in catalog}
    assert (by_id["kiss"]["zalo_icon"], by_id["kiss"]["r_type"]) == (":-*", 8)
    assert (by_id["fade"]["zalo_icon"], by_id["fade"]["r_type"]) == (
        "/-fade",
        121,
    )
    assert (by_id["love-you"]["zalo_icon"], by_id["love-you"]["r_type"]) == (
        "/-loveu",
        133,
    )
    database.dispose()


def test_saved_legacy_catalog_is_upgraded_without_losing_preferences(settings):
    database = Database(settings)
    database.create_schema()
    legacy_like = deepcopy(next(item for item in DEFAULT_REACTIONS if item["id"] == "like"))
    legacy_like.update(
        {
            "label": "Mã cũ không còn hợp lệ",
            "r_type": 999,
            "quick": False,
            "enabled": False,
        }
    )
    custom = {
        "id": "custom-fire",
        "label": "Bùng cháy",
        "glyph": "🔥",
        "zalo_icon": "🔥",
        "r_type": -1,
        "source": 6,
        "mode": "local",
        "quick": True,
        "enabled": True,
        "verified": False,
        "fallback": "none",
        "locked": False,
    }

    with database.session_factory() as session:
        session.add(
            AppSetting(
                key=REACTION_CATALOG_KEY,
                value=[legacy_like, custom],
            )
        )
        session.commit()
        upgraded = reaction_catalog(session, include_disabled=True)
        by_id = {item["id"]: item for item in upgraded}
        assert len(upgraded) == 55
        assert by_id["like"]["label"] == "Thích"
        assert by_id["like"]["r_type"] == 3
        assert by_id["like"]["enabled"] is False
        assert by_id["like"]["quick"] is False
        assert by_id["custom-fire"]["mode"] == "local"

        saved = save_reaction_catalog(session, [custom], updated_by_id=None)
        assert len(saved) == 55
        assert sum(item["quick"] and item["enabled"] for item in saved) == 7
        assert len(session.get(AppSetting, REACTION_CATALOG_KEY).value) == 55

    database.dispose()
