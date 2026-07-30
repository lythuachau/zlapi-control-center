from __future__ import annotations

from control_center.connector import ZaloConnector
from control_center.db import Database
from control_center.models import ZaloAccount, ZaloContactProfile
from control_center.profiles import normalize_profiles


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
        }
    ]


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
