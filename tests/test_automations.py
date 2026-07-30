from __future__ import annotations

from sqlalchemy import select

from control_center.automations import (
    MENTION_STICKER_KIND,
    mark_automation_run,
    mention_triggers,
)
from control_center.db import Database
from control_center.listener import store_listener_event
from control_center.models import AutomationRule, AutomationRun, Event, Job, ZaloAccount


def _message_payload(
    *,
    message_id: str,
    author_id: str = "member-1",
    content: str = "@Admin cho mình hỏi",
    mentions: list[dict] | None = None,
) -> dict:
    return {
        "message_id": message_id,
        "author_id": author_id,
        "message": content,
        "message_object": {
            "msgId": message_id,
            "cliMsgId": f"client-{message_id}",
            "msgType": "webchat",
            "content": content,
            "mentions": mentions
            if mentions is not None
            else [{"uid": "self-uid", "pos": 0, "len": 6}],
        },
        "thread_type": "GROUP",
    }


def _rule_config(*, direct: bool = True, mention_all: bool = False) -> dict:
    return {
        "group_ids": ["group-allowed"],
        "trigger_direct_mention": direct,
        "trigger_mention_all": mention_all,
        "cooldown_seconds": 30,
        "sticker": {
            "sticker_id": "15526",
            "category_id": "234",
            "sticker_type": 1,
            "thumbnail_url": (
                "https://zalo-api.zadn.vn/api/emoticon/sticker/"
                "webpc?eid=15526&size=130"
            ),
            "width": 130,
            "height": 130,
        },
    }


def _reaction_rule_config(*, count: int = 3) -> dict:
    config = _rule_config()
    config.update(
        {
            "response_type": "reaction",
            "reaction_count": count,
            "reaction": {
                "id": "heart",
                "label": "Yêu thích",
                "glyph": "❤️",
                "zalo_icon": "/-heart",
                "r_type": 5,
                "source": 6,
                "mode": "native",
                "verified": True,
            },
        }
    )
    config.pop("sticker")
    return config


def test_mention_detection_is_group_only_and_ignores_own_messages():
    direct = _message_payload(message_id="direct-1")
    assert mention_triggers(direct, "self-uid") == {"direct_mention"}

    own = _message_payload(message_id="own-1", author_id="self-uid")
    assert mention_triggers(own, "self-uid") == set()

    private = {**direct, "thread_type": "USER"}
    assert mention_triggers(private, "self-uid") == set()

    mention_all = _message_payload(
        message_id="all-1",
        content="@all vui lòng kiểm tra",
        mentions=[{"uid": "special-all-uid", "pos": 0, "len": 4}],
    )
    assert mention_triggers(mention_all, "self-uid") == {"mention_all"}


def test_listener_queues_selected_group_once_and_enforces_cooldown(settings):
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Automation account",
            zalo_uid="self-uid",
            cookie_cipher="encrypted-cookie",
            imei_cipher="encrypted-imei",
            listener_enabled=True,
            status="online",
            enabled=True,
        )
        session.add(account)
        session.flush()
        rule = AutomationRule(
            account_id=account.id,
            kind=MENTION_STICKER_KIND,
            name="Tag nhóm → Sticker",
            enabled=True,
            config=_rule_config(),
        )
        session.add(rule)
        session.commit()
        account_id = account.id
        rule_id = rule.id

    first_payload = _message_payload(message_id="mention-1")
    assert store_listener_event(
        database,
        account_id,
        "message.received",
        first_payload,
        "group-allowed",
    )
    assert not store_listener_event(
        database,
        account_id,
        "message.received",
        first_payload,
        "group-allowed",
    )
    assert store_listener_event(
        database,
        account_id,
        "message.received",
        _message_payload(message_id="mention-2"),
        "group-allowed",
    )
    assert store_listener_event(
        database,
        account_id,
        "message.received",
        _message_payload(message_id="mention-other"),
        "group-other",
    )

    with database.session_factory() as session:
        jobs = session.scalars(
            select(Job).where(Job.action == "__chat_automation_sticker__")
        ).all()
        assert len(jobs) == 1
        assert jobs[0].payload["thread_id"] == "group-allowed"
        assert jobs[0].payload["reply_message_id"] == "mention-1"
        assert jobs[0].max_attempts == 1

        runs = session.scalars(
            select(AutomationRun)
            .where(AutomationRun.rule_id == rule_id)
            .order_by(AutomationRun.created_at)
        ).all()
        assert [run.status for run in runs] == ["queued", "suppressed"]
        trigger_event = session.scalar(
            select(Event).where(Event.event_type == "automation.triggered")
        )
        assert trigger_event is not None
        assert trigger_event.payload["run_id"] == runs[0].id
        assert trigger_event.payload["job_id"] == jobs[0].id

        mark_automation_run(session, jobs[0].id, "succeeded")
        session.commit()
        assert session.get(AutomationRun, runs[0].id).status == "succeeded"

    database.dispose()


def test_listener_mention_all_can_be_enabled_without_direct_mention(settings):
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Mention all account",
            zalo_uid="self-uid",
            cookie_cipher="encrypted-cookie",
            imei_cipher="encrypted-imei",
            listener_enabled=True,
            status="online",
            enabled=True,
        )
        session.add(account)
        session.flush()
        session.add(
            AutomationRule(
                account_id=account.id,
                kind=MENTION_STICKER_KIND,
                name="Tag nhóm → Sticker",
                enabled=True,
                config=_rule_config(direct=False, mention_all=True),
            )
        )
        session.commit()
        account_id = account.id

    assert store_listener_event(
        database,
        account_id,
        "message.received",
        _message_payload(
            message_id="mention-all-1",
            content="@all thử tự động hóa",
            mentions=[{"uid": "special-all-uid", "pos": 0, "len": 4}],
        ),
        "group-allowed",
    )
    with database.session_factory() as session:
        job = session.scalar(
            select(Job).where(Job.action == "__chat_automation_sticker__")
        )
        assert job is not None
        assert job.payload["reply_message_id"] == "mention-all-1"

    database.dispose()


def test_listener_queues_configured_reaction_count_for_tagged_message(settings):
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Automation reaction account",
            zalo_uid="self-uid",
            cookie_cipher="encrypted-cookie",
            imei_cipher="encrypted-imei",
            listener_enabled=True,
            status="online",
            enabled=True,
        )
        session.add(account)
        session.flush()
        session.add(
            AutomationRule(
                account_id=account.id,
                kind=MENTION_STICKER_KIND,
                name="Tag nhóm → Phản hồi",
                enabled=True,
                config=_reaction_rule_config(count=4),
            )
        )
        session.commit()
        account_id = account.id

    assert store_listener_event(
        database,
        account_id,
        "message.received",
        _message_payload(message_id="reaction-tag-1"),
        "group-allowed",
    )
    with database.session_factory() as session:
        job = session.scalar(
            select(Job).where(Job.action == "__chat_automation_reaction__")
        )
        assert job is not None
        assert job.payload["repeatCount"] == 4
        assert job.payload["reactionIcon"] == "/-heart"
        assert job.payload["_chat_context"]["fallback"] == "none"
        run = session.scalar(select(AutomationRun).where(AutomationRun.job_id == job.id))
        assert run is not None
        assert run.detail["response_type"] == "reaction"
        assert run.detail["reaction_count"] == 4

    database.dispose()
