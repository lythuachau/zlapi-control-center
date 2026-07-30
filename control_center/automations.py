from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    AuditLog,
    AutomationRule,
    AutomationRun,
    Event,
    Job,
    ZaloAccount,
    utcnow,
)

MENTION_STICKER_KIND = "mention_sticker_reply"
DEFAULT_COOLDOWN_SECONDS = 30


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _message_object(payload: dict) -> dict:
    value = payload.get("message_object")
    return value if isinstance(value, dict) else {}


def _message_text(payload: dict) -> str:
    message_object = _message_object(payload)
    for value in (
        message_object.get("content"),
        message_object.get("message"),
        payload.get("message"),
    ):
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            for key in ("text", "content", "message", "title"):
                nested = value.get(key)
                if isinstance(nested, str) and nested.strip():
                    return nested
    return ""


def _positive_int(value: Any, fallback: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if 0 < parsed <= maximum else fallback


def _bounded_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return min(max(parsed, minimum), maximum)


def _mentions(payload: dict) -> list[dict]:
    value = _message_object(payload).get("mentions")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _mention_targets_all(message: str, mention: dict) -> bool:
    try:
        position = int(mention.get("pos"))
        length = int(mention.get("len"))
    except (TypeError, ValueError):
        return False
    if position < 0 or length <= 0:
        return False
    normalized = message.casefold()
    if normalized[position : position + length] == "@all":
        return True
    # Zalo uses UTF-16 positions. If an emoji appears before @all, Python's
    # Unicode index can differ, so retain the mention-length guard as fallback.
    return length == 4 and "@all" in normalized


def mention_triggers(payload: dict, account_uid: str | None) -> set[str]:
    if "group" not in _text(payload.get("thread_type")).casefold():
        return set()
    author_id = _text(payload.get("author_id"))
    normalized_account_uid = _text(account_uid)
    if normalized_account_uid and author_id == normalized_account_uid:
        return set()
    mentions = _mentions(payload)
    if not mentions:
        return set()
    triggers: set[str] = set()
    if normalized_account_uid and any(
        _text(mention.get("uid")) == normalized_account_uid
        for mention in mentions
    ):
        triggers.add("direct_mention")
    message = _message_text(payload)
    if message and any(_mention_targets_all(message, mention) for mention in mentions):
        triggers.add("mention_all")
    return triggers


def _source_message_id(payload: dict) -> str:
    message_object = _message_object(payload)
    for value in (
        message_object.get("msgId"),
        message_object.get("messageId"),
        message_object.get("globalMsgId"),
        payload.get("message_id"),
    ):
        candidate = _text(value)
        if candidate:
            return candidate
    return ""


def _safe_sticker(config: dict) -> dict | None:
    value = config.get("sticker")
    if not isinstance(value, dict):
        return None
    sticker_id = _text(value.get("sticker_id"))
    category_id = _text(value.get("category_id"))
    if (
        not sticker_id.isdigit()
        or not category_id.isdigit()
        or int(sticker_id) <= 0
        or int(category_id) <= 0
    ):
        return None
    thumbnail_url = _text(value.get("thumbnail_url")) or (
        "https://zalo-api.zadn.vn/api/emoticon/sticker/"
        f"webpc?eid={sticker_id}&size=130"
    )
    parsed = urlsplit(thumbnail_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return {
        "sticker_id": sticker_id,
        "category_id": category_id,
        "sticker_type": _bounded_int(value.get("sticker_type"), 1, 0, 100),
        "thumbnail_url": thumbnail_url,
        "width": _positive_int(value.get("width"), 130, 2000),
        "height": _positive_int(value.get("height"), 130, 2000),
    }


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def queue_mention_sticker_automation(
    session: Session,
    account: ZaloAccount,
    source_event: Event,
) -> tuple[AutomationRun | None, Job | None]:
    rule = session.scalar(
        select(AutomationRule).where(
            AutomationRule.account_id == account.id,
            AutomationRule.kind == MENTION_STICKER_KIND,
            AutomationRule.enabled.is_(True),
        )
    )
    if rule is None or source_event.id is None or not source_event.thread_id:
        return None, None
    if session.scalar(
        select(AutomationRun.id).where(
            AutomationRun.rule_id == rule.id,
            AutomationRun.source_event_id == source_event.id,
        )
    ):
        return None, None

    config = rule.config if isinstance(rule.config, dict) else {}
    group_ids = {
        _text(value)
        for value in config.get("group_ids", [])
        if _text(value)
    }
    if source_event.thread_id not in group_ids:
        return None, None

    detected = mention_triggers(source_event.payload or {}, account.zalo_uid)
    matched: list[str] = []
    if config.get("trigger_direct_mention") and "direct_mention" in detected:
        matched.append("direct_mention")
    if config.get("trigger_mention_all") and "mention_all" in detected:
        matched.append("mention_all")
    if not matched:
        return None, None

    trigger_kind = "+".join(matched)
    now = utcnow()
    cooldown_seconds = _bounded_int(
        config.get("cooldown_seconds"),
        DEFAULT_COOLDOWN_SECONDS,
        10,
        3600,
    )
    last_triggered_at = _aware(rule.last_triggered_at)
    if last_triggered_at and now - last_triggered_at < timedelta(seconds=cooldown_seconds):
        run = AutomationRun(
            rule_id=rule.id,
            account_id=account.id,
            source_event_id=source_event.id,
            trigger_kind=trigger_kind,
            status="suppressed",
            detail={"reason": "cooldown", "cooldown_seconds": cooldown_seconds},
            finished_at=now,
        )
        session.add(run)
        session.add(
            AuditLog(
                actor_type="system",
                action="automation.suppressed",
                resource_type="automation_rule",
                resource_id=rule.id,
                details={
                    "account_id": account.id,
                    "thread_id": source_event.thread_id,
                    "source_event_id": source_event.id,
                    "trigger_kind": trigger_kind,
                    "reason": "cooldown",
                },
            )
        )
        session.flush()
        return run, None

    sticker = _safe_sticker(config)
    source_message_id = _source_message_id(source_event.payload or {})
    if sticker is None or not source_message_id:
        return None, None
    job = Job(
        account_id=account.id,
        action="__chat_automation_sticker__",
        payload={
            "thread_id": source_event.thread_id,
            "thread_type": "group",
            "kind": "sticker",
            "media": sticker,
            "reply_message_id": source_message_id,
            "automation_rule_id": rule.id,
            "source_event_id": source_event.id,
            "_chat_context": {
                "automation": True,
                "kind": "sticker",
                "media": sticker,
                "reply": {
                    "event_id": source_event.id,
                    "message_id": source_message_id,
                },
            },
        },
        created_by_id=None,
        priority=35,
        max_attempts=1,
    )
    session.add(job)
    session.flush()
    run = AutomationRun(
        rule_id=rule.id,
        account_id=account.id,
        source_event_id=source_event.id,
        job_id=job.id,
        trigger_kind=trigger_kind,
        status="queued",
        detail={
            "thread_id": source_event.thread_id,
            "sticker_key": f"{sticker['category_id']}:{sticker['sticker_id']}",
        },
    )
    rule.last_triggered_at = now
    session.add(run)
    session.flush()
    session.add_all(
        [
            Event(
                account_id=account.id,
                event_type="automation.triggered",
                thread_id=source_event.thread_id,
                dedupe_key=hashlib.sha256(
                    f"{rule.id}:{source_event.id}".encode()
                ).hexdigest(),
                payload={
                    "rule_id": rule.id,
                    "run_id": run.id,
                    "job_id": job.id,
                    "source_event_id": source_event.id,
                    "trigger_kind": trigger_kind,
                },
            ),
            AuditLog(
                actor_type="system",
                action="automation.triggered",
                resource_type="automation_rule",
                resource_id=rule.id,
                details={
                    "account_id": account.id,
                    "thread_id": source_event.thread_id,
                    "source_event_id": source_event.id,
                    "job_id": job.id,
                    "trigger_kind": trigger_kind,
                },
            ),
        ]
    )
    session.flush()
    return run, job


def mark_automation_run(
    session: Session,
    job_id: str,
    status: str,
    *,
    error: str | None = None,
) -> None:
    run = session.scalar(select(AutomationRun).where(AutomationRun.job_id == job_id))
    if run is None:
        return
    run.status = status
    run.finished_at = utcnow() if status in {"succeeded", "failed", "cancelled"} else None
    detail = dict(run.detail or {})
    if error:
        detail["error"] = error[:1000]
    run.detail = detail
    rule = session.get(AutomationRule, run.rule_id)
    if rule is None:
        return
    config = dict(rule.config or {})
    if status == "succeeded":
        config["consecutive_failures"] = 0
        config.pop("disabled_reason", None)
    elif status == "failed":
        failures = max(int(config.get("consecutive_failures") or 0), 0) + 1
        config["consecutive_failures"] = failures
        if failures >= 3:
            rule.enabled = False
            config["disabled_reason"] = "three_consecutive_failures"
    rule.config = config


def automation_rule_payload(
    session: Session,
    rule: AutomationRule | None,
    *,
    account: ZaloAccount,
) -> dict:
    config = rule.config if rule and isinstance(rule.config, dict) else {}
    last_run = None
    run_counts: dict[str, int] = {}
    if rule:
        last_run = session.scalar(
            select(AutomationRun)
            .where(AutomationRun.rule_id == rule.id)
            .order_by(AutomationRun.created_at.desc())
            .limit(1)
        )
        run_counts = {
            str(status): int(count)
            for status, count in session.execute(
                select(AutomationRun.status, func.count(AutomationRun.id))
                .where(AutomationRun.rule_id == rule.id)
                .group_by(AutomationRun.status)
            ).all()
        }
    return {
        "id": rule.id if rule else None,
        "kind": MENTION_STICKER_KIND,
        "name": rule.name if rule else "Tag nhóm → Sticker",
        "enabled": bool(rule.enabled) if rule else False,
        "account_id": account.id,
        "account_ready": bool(
            account.enabled
            and account.listener_enabled
            and account.status == "online"
        ),
        "account_uid_ready": bool(account.zalo_uid),
        "group_ids": list(config.get("group_ids") or []),
        "trigger_direct_mention": bool(config.get("trigger_direct_mention", True)),
        "trigger_mention_all": bool(config.get("trigger_mention_all", True)),
        "sticker": _safe_sticker(config),
        "cooldown_seconds": _bounded_int(
            config.get("cooldown_seconds"),
            DEFAULT_COOLDOWN_SECONDS,
            10,
            3600,
        ),
        "consecutive_failures": _bounded_int(
            config.get("consecutive_failures"), 0, 0, 1000
        ),
        "disabled_reason": _text(config.get("disabled_reason")) or None,
        "last_triggered_at": rule.last_triggered_at if rule else None,
        "updated_at": rule.updated_at if rule else None,
        "run_counts": run_counts,
        "last_run": (
            {
                "id": last_run.id,
                "status": last_run.status,
                "trigger_kind": last_run.trigger_kind,
                "detail": last_run.detail,
                "created_at": last_run.created_at,
                "finished_at": last_run.finished_at,
            }
            if last_run
            else None
        ),
    }
