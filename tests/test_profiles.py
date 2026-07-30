from __future__ import annotations

import pytest

from control_center.connector import ZaloConnector, ZaloResponseError
from control_center.db import Database
from control_center.models import ZaloAccount, ZaloContactProfile
from control_center.profiles import (
    apply_relationship_action,
    backfill_friend_profiles,
    normalize_profiles,
)


class FakeProfileClient:
    def __init__(self):
        self.requested: list[str] = []

    def fetchUserInfo(self, user_ids):
        self.requested = list(user_ids)
        return {
            "profiles": {
                "visitor-1": {
                    "userId": "visitor-1",
                    "displayName": "Khách Một",
                    "avatar": "//s120-26-ava-talk.zadn.vn/visitor-1.jpg",
                    "accountStatus": 1,
                },
                "visitor-2": {
                    "userId": "visitor-2",
                    "zaloName": "Khách Hai",
                    "fullAvt": "https://s120-26-ava-talk.zadn.vn/visitor-2.jpg",
                },
            }
        }


class RejectedRelationshipClient:
    def blockUser(self, userId):
        assert userId == "person-rejected"
        return {"error_code": 403, "error_message": "not allowed"}


def test_normalize_profiles_supports_profile_maps_and_protocol_relative_avatars():
    profiles = normalize_profiles(
        {
            "profiles": {
                "person-1": {
                    "displayName": "Người Một",
                    "avatar_25": "//s120-ava-talk.zadn.vn/person-1.jpg",
                }
            }
        }
    )
    assert profiles == [
        {
            "user_id": "person-1",
            "name": "Người Một",
            "initials": "NM",
            "avatar_url": "https://s120-ava-talk.zadn.vn/person-1.jpg",
            "online": None,
            "phone": None,
            "relationship_status": "unknown",
            "blocked_by_me": False,
        }
    ]


def test_normalize_profiles_derives_relationship_state():
    profiles = normalize_profiles(
        {
            "profiles": {
                "friend-1": {"displayName": "Bạn Một", "isFr": 1},
                "blocked-2": {
                    "displayName": "Bạn Hai",
                    "isBlockedByMe": True,
                },
                "pending-3": {
                    "displayName": "Bạn Ba",
                    "friendStatus": "request_received",
                },
            }
        }
    )
    by_id = {item["user_id"]: item for item in profiles}
    assert by_id["friend-1"]["relationship_status"] == "friend"
    assert by_id["blocked-2"]["relationship_status"] == "blocked"
    assert by_id["blocked-2"]["blocked_by_me"] is True
    assert by_id["pending-3"]["relationship_status"] == "pending_incoming"


def test_connector_fetches_and_caches_private_chat_profiles(settings):
    settings.ensure_directories()
    database = Database(settings)
    database.create_schema()
    fake_client = FakeProfileClient()
    connector = ZaloConnector(settings, object())
    connector.client_for = lambda _account: fake_client

    with database.session_factory() as session:
        account = ZaloAccount(
            name="Private profiles",
            zalo_uid="self-uid",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        session.add(account)
        session.commit()

        result = connector.fetch_chat_profiles(
            session,
            account,
            {
                "user_ids": [
                    "visitor-1",
                    "visitor-2",
                    "visitor-3",
                    "self-uid",
                ]
            },
        )
        rows = {
            row.user_id: row
            for row in session.query(ZaloContactProfile).all()
        }

    assert fake_client.requested == ["visitor-1", "visitor-2", "visitor-3"]
    assert result["count"] == 3
    assert rows["visitor-1"].name == "Khách Một"
    assert rows["visitor-1"].avatar_url.startswith("https://")
    assert rows["visitor-2"].name == "Khách Hai"
    assert rows["visitor-3"].name == "Zalo itor-3"
    assert rows["visitor-3"].avatar_url is None
    assert "self-uid" not in rows
    database.dispose()


def test_apply_relationship_action_updates_cached_profile(settings):
    settings.ensure_directories()
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Relationship account",
            cookie_cipher="unused",
            imei_cipher="unused",
        )
        session.add(account)
        session.commit()
        account_id = account.id

        profile = apply_relationship_action(
            session,
            account_id=account_id,
            user_id="person-42",
            action="blockUser",
        )
        assert profile.relationship_status == "blocked"
        assert profile.blocked_by_me is True

        profile = apply_relationship_action(
            session,
            account_id=account_id,
            user_id="person-42",
            action="unblockUser",
        )
        assert profile.relationship_status == "unknown"
        assert profile.blocked_by_me is False
    database.dispose()


def test_backfill_friend_profiles_preserves_action_state(settings):
    settings.ensure_directories()
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Friend backfill account",
            cookie_cipher="unused",
            imei_cipher="unused",
        )
        session.add(account)
        session.commit()
        account_id = account.id
        session.add_all(
            [
                ZaloContactProfile(
                    account_id=account_id,
                    user_id="unknown-profile",
                    name="Zalo rofile",
                    relationship_status="unknown",
                    source="fetchUserInfo",
                ),
                ZaloContactProfile(
                    account_id=account_id,
                    user_id="blocked-profile",
                    name="Blocked profile",
                    relationship_status="blocked",
                    blocked_by_me=True,
                    source="blockUser",
                ),
                ZaloContactProfile(
                    account_id=account_id,
                    user_id="unblocked-profile",
                    name="Unblocked profile",
                    relationship_status="unknown",
                    source="unblockUser",
                ),
            ]
        )
        session.commit()

        changed = backfill_friend_profiles(
            session,
            account_id=account_id,
            result=[
                {"userId": "new-friend", "displayName": "New Friend"},
                {"userId": "unknown-profile", "displayName": "Known Friend"},
                {"userId": "blocked-profile", "displayName": "Blocked profile"},
                {"userId": "unblocked-profile", "displayName": "Unblocked profile"},
            ],
        )
        session.commit()

        assert changed == 2
        assert session.get(
            ZaloContactProfile,
            (account_id, "new-friend"),
        ).relationship_status == "friend"
        assert session.get(
            ZaloContactProfile,
            (account_id, "unknown-profile"),
        ).relationship_status == "friend"
        blocked = session.get(
            ZaloContactProfile,
            (account_id, "blocked-profile"),
        )
        assert blocked.relationship_status == "blocked"
        assert blocked.blocked_by_me is True
        unblocked = session.get(
            ZaloContactProfile,
            (account_id, "unblocked-profile"),
        )
        assert unblocked.relationship_status == "unknown"
        assert unblocked.source == "unblockUser"
    database.dispose()


def test_relationship_connector_rejects_zalo_error_payload(settings):
    settings.ensure_directories()
    database = Database(settings)
    database.create_schema()
    connector = ZaloConnector(settings, object())
    connector.client_for = lambda _account: RejectedRelationshipClient()
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Rejected relationship account",
            cookie_cipher="unused",
            imei_cipher="unused",
            enabled=True,
        )
        session.add(account)
        session.commit()
        with pytest.raises(ZaloResponseError, match=r"Zalo #403"):
            connector.execute(
                session,
                account,
                "blockUser",
                {"userId": "person-rejected"},
            )
    database.dispose()
