from __future__ import annotations

from control_center.connector import ZaloConnector
from control_center.db import Database
from control_center.models import ZaloAccount


class FakeGroupClient:
    def __init__(self):
        self.profile_batches: list[list[str]] = []

    def fetchGroupInfo(self, group_id):
        assert group_id == "group-101"
        return {
            "gridInfoMap": {
                "group-101": {
                    "groupId": "group-101",
                    "name": "Nhóm vận hành",
                    "desc": "Nhóm thử nghiệm",
                    "fullAvt": "https://photo.zalo.me/group.jpg",
                    "creatorId": "owner-1",
                    "adminIds": ["admin-2"],
                    "memberIds": ["member-3", "owner-1", "admin-2"],
                    "totalMember": 3,
                }
            }
        }

    def fetchUserInfo(self, member_ids):
        self.profile_batches.append(list(member_ids))
        return {
            "profiles": {
                "owner-1": {
                    "id": "owner-1",
                    "displayName": "Chủ Nhóm",
                    "avatar": "https://photo.zalo.me/owner.jpg",
                    "accountStatus": 1,
                },
                "admin-2": {
                    "id": "admin-2",
                    "displayName": "Quản Trị Viên",
                    "avatar": "https://photo.zalo.me/admin.jpg",
                    "accountStatus": 1,
                },
                "member-3": {
                    "id": "member-3",
                    "displayName": "Thành Viên",
                    "avatar": "",
                    "accountStatus": 0,
                },
            }
        }

    def getGroupPinMsg(self, group_id, page=1, count=20):
        assert group_id == "group-101"
        assert page == 1
        assert count == 50
        return {
            "items": [
                {
                    "id": "pin-topic-1",
                    "type": 2,
                    "emoji": "📌",
                    "startTime": 1721000000000,
                    "params": (
                        '{"client_msg_id":"cli-pin-1",'
                        '"global_msg_id":"global-pin-1",'
                        '"senderUid":"member-3",'
                        '"senderName":"Thành Viên",'
                        '"title":"Thông báo họp lúc 14:00",'
                        '"msg_type":1}'
                    ),
                }
            ]
        }

    def getGroupNote(self, group_id, page=1, count=20):
        assert group_id == "group-101"
        assert page == 1
        assert count == 50
        return {
            "items": [
                {
                    "id": "note-topic-1",
                    "type": 1,
                    "emoji": "📝",
                    "color": -14540254,
                    "startTime": 1721000000000,
                    "params": (
                        '{"title":"Lịch trực tuần này",'
                        '"creatorId":"owner-1",'
                        '"creatorName":"Chủ Nhóm",'
                        '"editorId":"admin-2",'
                        '"editorName":"Quản Trị"}'
                    ),
                }
            ]
        }

    def getGroupPoll(self, group_id, page=1, count=20):
        assert group_id == "group-101"
        assert page == 1
        assert count == 50
        return {
            "items": [
                {
                    "poll_id": "poll-1",
                    "question": "Chon lich hop",
                    "options": [
                        {"option_id": "opt-1", "content": "Thu Hai", "votes": 4, "voted": True},
                        {"option_id": "opt-2", "content": "Thu Ba", "votes": 2, "voted": False},
                    ],
                    "num_vote": 6,
                    "closed": False,
                    "allow_multi_choices": False,
                    "is_anonymous": True,
                    "is_hide_vote_preview": False,
                    "created_time": 1721000000000,
                }
            ]
        }

    def viewPollDetail(self, poll_id):
        assert str(poll_id) == "poll-1"
        return {
            "poll_id": "poll-1",
            "question": "Chon lich hop",
            "options": [
                {"option_id": "opt-1", "content": "Thu Hai", "votes": 5, "voted": True},
                {"option_id": "opt-2", "content": "Thu Ba", "votes": 3, "voted": False},
            ],
            "num_vote": 8,
            "closed": True,
            "allow_multi_choices": False,
            "is_anonymous": True,
            "expired_time": 1721003600000,
        }


def test_connector_builds_compact_group_member_snapshot(settings):
    settings.ensure_directories()
    database = Database(settings)
    database.create_schema()
    fake_client = FakeGroupClient()
    connector = ZaloConnector(settings, object())
    connector.client_for = lambda _account: fake_client

    with database.session_factory() as session:
        account = ZaloAccount(
            name="Group connector",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        session.add(account)
        session.commit()

        result = connector.fetch_chat_group_info(
            session,
            account,
            {"thread_id": "group-101"},
        )

    assert fake_client.profile_batches == [["member-3", "owner-1", "admin-2"]]
    assert result["group"] == {
        "thread_id": "group-101",
        "name": "Nhóm vận hành",
        "description": "Nhóm thử nghiệm",
        "avatar_url": "https://photo.zalo.me/group.jpg",
        "member_count": 3,
        "owner_id": "owner-1",
        "admin_ids": ["admin-2"],
        "has_more_members": False,
    }
    assert [(item["user_id"], item["role"]) for item in result["members"]] == [
        ("owner-1", "owner"),
        ("admin-2", "admin"),
        ("member-3", "member"),
    ]
    database.dispose()


def test_connector_builds_compact_group_pin_snapshot(settings):
    settings.ensure_directories()
    database = Database(settings)
    database.create_schema()
    fake_client = FakeGroupClient()
    connector = ZaloConnector(settings, object())
    connector.client_for = lambda _account: fake_client

    with database.session_factory() as session:
        account = ZaloAccount(
            name="Group pin connector",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        session.add(account)
        session.commit()

        result = connector.fetch_chat_group_pins(
            session,
            account,
            {"thread_id": "group-101"},
        )

    assert result == {
        "thread_id": "group-101",
        "pins": [
            {
                "pin_id": "pin-topic-1",
                "message_id": "global-pin-1",
                "cli_message_id": "cli-pin-1",
                "author_id": "member-3",
                "author_name": "Thành Viên",
                "title": "Thông báo họp lúc 14:00",
                "message_type": "1",
                "thumbnail_url": None,
                "pinned_at": 1721000000000,
                "emoji": "📌",
            }
        ],
        "count": 1,
        "limit": 50,
    }
    database.dispose()


def test_connector_builds_compact_group_note_snapshot(settings):
    settings.ensure_directories()
    database = Database(settings)
    database.create_schema()
    fake_client = FakeGroupClient()
    connector = ZaloConnector(settings, object())
    connector.client_for = lambda _account: fake_client

    with database.session_factory() as session:
        account = ZaloAccount(
            name="Group note connector",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        session.add(account)
        session.commit()

        result = connector.fetch_chat_group_notes(
            session,
            account,
            {"thread_id": "group-101"},
        )

    assert result == {
        "thread_id": "group-101",
        "notes": [
            {
                "note_id": "note-topic-1",
                "content": "Lịch trực tuần này",
                "creator_id": "owner-1",
                "creator_name": "Chủ Nhóm",
                "editor_id": "admin-2",
                "editor_name": "Quản Trị",
                "created_at": 1721000000000,
                "updated_at": None,
                "emoji": "📝",
                "color": "-14540254",
            }
        ],
        "count": 1,
        "limit": 50,
    }
    database.dispose()


def test_connector_builds_compact_group_poll_and_detail_snapshots(settings):
    settings.ensure_directories()
    database = Database(settings)
    database.create_schema()
    fake_client = FakeGroupClient()
    connector = ZaloConnector(settings, object())
    connector.client_for = lambda _account: fake_client

    with database.session_factory() as session:
        account = ZaloAccount(
            name="Group poll connector",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        session.add(account)
        session.commit()

        listed = connector.fetch_chat_group_polls(
            session,
            account,
            {"thread_id": "group-101"},
        )
        detail = connector.fetch_chat_group_poll_detail(
            session,
            account,
            {"thread_id": "group-101", "poll_id": "poll-1"},
        )

    assert listed["polls"][0]["poll_id"] == "poll-1"
    assert listed["polls"][0]["total_votes"] == 6
    assert listed["polls"][0]["options"][0] == {
        "option_id": "opt-1",
        "label": "Thu Hai",
        "vote_count": 4,
        "voter_count": None,
        "voted": True,
    }
    assert detail["poll"]["status"] == "ended"
    assert detail["poll"]["total_votes"] == 8
    database.dispose()
