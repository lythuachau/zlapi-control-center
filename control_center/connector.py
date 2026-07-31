from __future__ import annotations

import json
import secrets
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from zlapi import Message, MessageObject, ThreadType, ZaloAPI
from zlapi._exception import (
    DecodePayloadError,
    EncodePayloadError,
    LoginMethodNotSupport,
    ZaloLoginError,
)

from .catalog import ActionSpec, get_action
from .config import Settings
from .gif import PreparedGif, prepare_gif_for_zalo
from .media import gif_library_media_url, outbound_media_url, validate_media_kind
from .models import GifLibraryItem, MediaUpload, ZaloAccount, utcnow
from .profiles import cache_profiles, canonical_profile_user_id
from .security import SecretBox, is_expired, safe_child_path
from .video import VideoMetadata, generate_video_thumbnail, probe_video


class UnsupportedActionError(RuntimeError):
    pass


class CircuitOpenError(RuntimeError):
    pass


class ZaloResponseError(ValueError):
    pass


class AccountIdentityError(ValueError):
    """Raised when credentials cannot be safely bound to an account history."""

    code = "account_identity_error"


class AccountIdentityMismatchError(AccountIdentityError):
    code = "session_identity_mismatch"


class AccountIdentityConflictError(AccountIdentityError):
    code = "session_identity_conflict"


class TokenBucket:
    def __init__(self, rate: float = 1.0, capacity: float = 3.0):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                delay = (1 - self.tokens) / self.rate
            time.sleep(delay)


_limiters: dict[str, TokenBucket] = {}
_limiters_lock = threading.Lock()
AUTOMATION_REACTION_INTERVAL_SECONDS = 0.8
SESSION_FAILURE_TYPES = (
    ZaloLoginError,
    LoginMethodNotSupport,
    EncodePayloadError,
    DecodePayloadError,
)


def _limiter(account_id: str) -> TokenBucket:
    with _limiters_lock:
        return _limiters.setdefault(account_id, TokenBucket())


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value)}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if hasattr(value, "value"):
        return jsonable(value.value)
    if hasattr(value, "__dict__"):
        return {
            key: jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


def _find_zalo_error(value: Any) -> tuple[Any, str] | None:
    if isinstance(value, dict):
        if "error_code" in value:
            code = value.get("error_code")
            if code not in (None, "", 0, "0"):
                return code, str(value.get("error_message") or value.get("message") or "Zalo từ chối yêu cầu")
        for item in value.values():
            found = _find_zalo_error(item)
            if found:
                return found
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            found = _find_zalo_error(item)
            if found:
                return found
    return None


def ensure_zalo_success(value: Any, operation: str) -> Any:
    payload = jsonable(value)
    error = _find_zalo_error(payload)
    if error:
        code, message = error
        raise ZaloResponseError(f"{operation} thất bại (Zalo #{code}): {message}")
    return payload


def _snapshot_text(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _nested_value(value: Any, names: set[str]) -> Any:
    """Find a scalar in loosely shaped Zalo payloads without retaining raw data."""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return _nested_value(decoded, names)
    if isinstance(value, dict):
        normalized = {
            str(key).replace("_", "").lower(): item
            for key, item in value.items()
        }
        for name in names:
            candidate = normalized.get(name)
            if isinstance(candidate, (dict, list, tuple)):
                nested = _nested_value(candidate, names)
                if nested not in (None, "", [], {}):
                    return nested
                continue
            if candidate not in (None, "", [], {}):
                return candidate
        for item in value.values():
            if isinstance(item, (dict, list, tuple, str)):
                candidate = _nested_value(item, names)
                if candidate not in (None, "", [], {}):
                    return candidate
    elif isinstance(value, (list, tuple)):
        for item in value:
            candidate = _nested_value(item, names)
            if candidate not in (None, "", [], {}):
                return candidate
    return None


def _group_pin_records(value: Any) -> list[dict]:
    """Normalize Group Board type 2 records returned by different Zalo versions."""
    records: list[dict] = []
    seen: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, str):
            try:
                decoded = json.loads(item)
            except (TypeError, ValueError):
                return
            walk(decoded)
            return
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict):
            return

        message_id = _snapshot_text(
            _nested_value(item, {"globalmsgid", "msgid", "messageid"})
        )
        cli_message_id = _snapshot_text(
            _nested_value(item, {"clientmsgid", "climsgid"})
        )
        if message_id or cli_message_id:
            pin_id = _snapshot_text(
                _nested_value(item, {"topicid", "pinid", "boardid", "id"})
            )
            identity = message_id or cli_message_id or pin_id
            if identity and identity not in seen:
                seen.add(identity)
                title = _snapshot_text(
                    _nested_value(
                        item,
                        {"title", "content", "description", "filename"},
                    )
                )
                thumbnail = _snapshot_text(
                    _nested_value(item, {"thumb", "thumbnail", "thumbnailurl"})
                )
                if thumbnail and not thumbnail.startswith(("https://", "http://")):
                    thumbnail = None
                pinned_at = _nested_value(
                    item,
                    {"starttime", "pintime", "createtime", "createdat", "timestamp"},
                )
                if not isinstance(pinned_at, (str, int, float)) or isinstance(
                    pinned_at,
                    bool,
                ):
                    pinned_at = None
                records.append(
                    {
                        "pin_id": pin_id,
                        "message_id": message_id,
                        "cli_message_id": cli_message_id,
                        "author_id": _snapshot_text(
                            _nested_value(
                                item,
                                {"senderuid", "senderid", "uidfrom", "ownerid"},
                            )
                        ),
                        "author_name": _snapshot_text(
                            _nested_value(
                                item,
                                {"sendername", "authorname", "dname"},
                            )
                        ),
                        "title": title or "Tin nhắn đã ghim",
                        "message_type": _snapshot_text(
                            _nested_value(item, {"msgtype", "messagetype"})
                        ),
                        "thumbnail_url": thumbnail,
                        "pinned_at": pinned_at,
                        "emoji": _snapshot_text(_nested_value(item, {"emoji"})),
                    }
                )
        for child in item.values():
            if isinstance(child, (dict, list, tuple, str)):
                walk(child)

    walk(value)
    return records[:100]


def _group_note_records(value: Any) -> list[dict]:
    """Normalize Group Board type 1 notes without persisting the raw response."""
    records: list[dict] = []
    seen: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, str):
            try:
                decoded = json.loads(item)
            except (TypeError, ValueError):
                return
            walk(decoded)
            return
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict):
            return

        normalized = {
            str(key).replace("_", "").lower(): child
            for key, child in item.items()
        }
        board_type = normalized.get("boardtype")
        if board_type is None:
            board_type = normalized.get("topictype", normalized.get("type"))
        note_id = _snapshot_text(
            _nested_value(item, {"noteid", "topicid", "boardid", "id"})
        )
        content = _snapshot_text(
            _nested_value(item, {"title", "content", "text", "description"})
        )
        is_note = board_type in (None, "", 1, "1")
        if note_id and content and is_note and note_id not in seen:
            seen.add(note_id)
            created_at = _nested_value(
                item,
                {"starttime", "createtime", "createdat", "timestamp"},
            )
            updated_at = _nested_value(
                item,
                {"updatetime", "updatedat", "modifiedtime", "edittime"},
            )
            if not isinstance(created_at, (str, int, float)) or isinstance(
                created_at,
                bool,
            ):
                created_at = None
            if not isinstance(updated_at, (str, int, float)) or isinstance(
                updated_at,
                bool,
            ):
                updated_at = None
            records.append(
                {
                    "note_id": note_id,
                    "content": content[:4000],
                    "creator_id": _snapshot_text(
                        _nested_value(
                            item,
                            {"creatorid", "ownerid", "authorid", "uidfrom"},
                        )
                    ),
                    "creator_name": _snapshot_text(
                        _nested_value(
                            item,
                            {"creatorname", "authorname", "sendername", "dname"},
                        )
                    ),
                    "editor_id": _snapshot_text(
                        _nested_value(item, {"editorid", "modifierid"})
                    ),
                    "editor_name": _snapshot_text(
                        _nested_value(item, {"editorname", "modifiername"})
                    ),
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "emoji": _snapshot_text(_nested_value(item, {"emoji"})),
                    "color": _snapshot_text(_nested_value(item, {"color"})),
                }
            )
        for child in item.values():
            if isinstance(child, (dict, list, tuple, str)):
                walk(child)

    walk(value)
    return records[:100]


def _group_poll_records(value: Any) -> list[dict]:
    """Normalize group poll records while dropping the raw Zalo response."""
    records: list[dict] = []
    seen: set[str] = set()

    def scalar(item: dict, names: set[str]) -> Any:
        normalized = {
            str(key).replace("_", "").lower(): child
            for key, child in item.items()
        }
        for name in names:
            candidate = normalized.get(name)
            if candidate not in (None, "", [], {}):
                return candidate
        return None

    def as_list(value: Any) -> list[Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return []
        if isinstance(value, dict):
            for key in ("items", "options", "answers", "choices", "data"):
                candidate = value.get(key)
                if isinstance(candidate, list):
                    return candidate
            return [value]
        return list(value) if isinstance(value, (list, tuple)) else []

    def number(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    def boolean(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def option_records(value: Any) -> list[dict]:
        options: list[dict] = []
        for index, raw in enumerate(as_list(value)):
            if isinstance(raw, str):
                raw = {"text": raw}
            if not isinstance(raw, dict):
                continue
            option_id = _snapshot_text(
                scalar(raw, {"optionid", "answerid", "choiceid", "id"})
            )
            label = _snapshot_text(
                scalar(raw, {"text", "title", "content", "name", "option", "label"})
            )
            if not label:
                continue
            vote_count = number(
                scalar(raw, {"votecount", "votes", "count", "totalvotes", "numberofvotes"})
            )
            voters = scalar(raw, {"voters", "voterids", "users"})
            voter_count = len(voters) if isinstance(voters, list) else None
            options.append(
                {
                    "option_id": option_id or f"option-{index + 1}",
                    "label": label[:500],
                    "vote_count": vote_count if vote_count is not None else voter_count or 0,
                    "voter_count": voter_count,
                    "voted": boolean(scalar(raw, {"voted", "hasvoted", "selected"})),
                }
            )
        return options[:100]

    def walk(item: Any) -> None:
        if isinstance(item, str):
            try:
                decoded = json.loads(item)
            except (TypeError, ValueError):
                return
            walk(decoded)
            return
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict):
            return

        normalized = {
            str(key).replace("_", "").lower(): child
            for key, child in item.items()
        }
        options_value = scalar(
            item,
            {"options", "optionlist", "answers", "choices", "polloptions", "listoptions"},
        )
        options = option_records(options_value)
        question = _snapshot_text(
            scalar(item, {"question", "pollquestion", "title", "content", "description", "name"})
        )
        poll_id = _snapshot_text(
            scalar(item, {"pollid", "poll_id", "pollkey", "topicid", "boardid"})
        )
        pollish = bool(
            poll_id
            and (
                question
                or options
                or any(
                    key in normalized
                    for key in (
                        "pollid",
                        "pollquestion",
                        "options",
                        "optionlist",
                        "answers",
                        "choices",
                        "votecount",
                        "totalvotes",
                        "expiredtime",
                    )
                )
            )
        )
        if pollish and poll_id not in seen:
            seen.add(poll_id)
            total_votes = number(
                scalar(item, {"totalvotes", "votecount", "votes", "totalvote", "numvote"})
            )
            if total_votes is None:
                total_votes = sum(option["vote_count"] for option in options)
            status_value = _snapshot_text(
                scalar(item, {"status", "state", "pollstatus"})
            )
            ended_value = scalar(
                item,
                {"isended", "ended", "isclosed", "closed", "islocked", "locked", "expired"},
            )
            ended = bool(ended_value) if isinstance(ended_value, bool) else str(ended_value).lower() in {
                "1",
                "true",
                "yes",
                "ended",
                "closed",
                "locked",
                "expired",
            }
            records.append(
                {
                    "poll_id": poll_id,
                    "question": (question or "Bình chọn trong nhóm")[:1000],
                    "options": options,
                    "total_votes": total_votes,
                    "status": "ended" if ended else (status_value or "active")[:40],
                    "created_at": scalar(item, {"createtime", "createdtime", "createdat", "starttime", "timestamp"}),
                    "updated_at": scalar(item, {"updatetime", "updatedtime", "updatedat", "edittime"}),
                    "expired_at": scalar(item, {"expiredtime", "expiredat", "endtime", "deadline"}),
                    "creator_id": _snapshot_text(scalar(item, {"creatorid", "ownerid", "authorid"})),
                    "creator_name": _snapshot_text(scalar(item, {"creatorname", "ownername", "authorname"})),
                    "allow_multiple": boolean(scalar(item, {"allowmultichoices", "allowmultiple", "multichoice", "ismultiple"})),
                    "allow_add_new_option": boolean(scalar(item, {"allowaddnewoption", "allowaddoption"})),
                    "anonymous": boolean(scalar(item, {"isanonymous", "anonymous"})),
                    "results_hidden": boolean(scalar(item, {"ishidevotepreview", "hidevotepreview", "resultshidden"})),
                    "joined": boolean(scalar(item, {"joined", "hasjoined", "voted"})),
                }
            )
        for child in item.values():
            if isinstance(child, (dict, list, tuple, str)):
                walk(child)

    walk(value)
    return records[:100]


def _profile_records(value: Any) -> list[tuple[str | None, dict]]:
    if isinstance(value, list):
        records: list[tuple[str | None, dict]] = []
        for item in value:
            records.extend(_profile_records(item))
        return records
    if not isinstance(value, dict):
        return []
    profiles = value.get("profiles")
    if isinstance(profiles, (dict, list)):
        return _profile_records(profiles)
    identity_keys = {"id", "userId", "user_id", "uid", "globalId"}
    if identity_keys.intersection(value):
        return [(None, value)]
    records = []
    for key, item in value.items():
        if not isinstance(item, dict):
            continue
        if identity_keys.intersection(item):
            records.append((str(key), item))
        else:
            records.extend(_profile_records(item))
    return records


def _compact_member_profile(item: dict, fallback_id: str | None = None) -> dict | None:
    user_id = next(
        (
            _snapshot_text(item.get(key))
            for key in ("id", "userId", "user_id", "uid", "globalId")
            if _snapshot_text(item.get(key))
        ),
        None,
    ) or fallback_id
    if not user_id:
        return None
    name = next(
        (
            _snapshot_text(item.get(key))
            for key in ("displayName", "dName", "zaloName", "fullName", "name")
            if _snapshot_text(item.get(key))
        ),
        None,
    )
    avatar_url = next(
        (
            _snapshot_text(item.get(key))
            for key in ("avatar", "avatar_25", "avatarUrl", "avt", "fullAvt")
            if _snapshot_text(item.get(key))
        ),
        None,
    )
    online = item.get("online")
    if online is None:
        online = item.get("isOnline")
    if online is None:
        account_status = item.get("accountStatus")
        online = bool(account_status) if account_status is not None else None
    return {
        "user_id": str(user_id),
        "name": name or f"Zalo {str(user_id)[-6:]}",
        "avatar_url": avatar_url,
        "online": bool(online) if online is not None else None,
    }


class ZaloConnector:
    def __init__(self, settings: Settings, secret_box: SecretBox):
        self.settings = settings
        self.secret_box = secret_box

    def client_for(self, account: ZaloAccount) -> ZaloAPI:
        cookies = self.secret_box.decrypt_json(account.cookie_cipher)
        imei = self.secret_box.decrypt_text(account.imei_cipher)
        return self.client_for_session(
            cookies=cookies,
            imei=imei,
            user_agent=account.user_agent,
        )

    @staticmethod
    def client_for_session(
        *,
        cookies: dict[str, str],
        imei: str,
        user_agent: str | None,
    ) -> ZaloAPI:
        return ZaloAPI(
            phone="cookie-session",
            password="cookie-session",
            imei=imei,
            session_cookies=cookies,
            user_agent=user_agent,
            auto_login=True,
        )

    @staticmethod
    def identity_from_payload(payload: Any, client_uid: Any = None) -> str:
        normalized = jsonable(payload)
        profile = normalized.get("profile", {}) if isinstance(normalized, dict) else {}
        candidate = profile.get("userId") if isinstance(profile, dict) else None
        uid = str(candidate or client_uid or "").strip()
        if not uid:
            raise AccountIdentityError(
                "Zalo không trả về UID nên không thể xác minh an toàn phiên này."
            )
        return uid

    def probe_session(
        self,
        *,
        cookies: dict[str, str],
        imei: str,
        user_agent: str | None,
    ) -> tuple[str, dict]:
        """Validate candidate secrets without changing the stored account session."""
        client = self.client_for_session(
            cookies=cookies,
            imei=imei,
            user_agent=user_agent,
        )
        payload = jsonable(client.fetchAccountInfo())
        uid = self.identity_from_payload(payload, client.uid)
        return uid, payload

    @staticmethod
    def validate_identity(
        session: Session,
        account: ZaloAccount,
        candidate_uid: str,
    ) -> None:
        current_uid = str(account.zalo_uid or "").strip()
        if current_uid and current_uid != candidate_uid:
            raise AccountIdentityMismatchError(
                "Cookie/IMEI mới thuộc một tài khoản Zalo khác. "
                "Phiên cũ và toàn bộ lịch sử vẫn được giữ nguyên."
            )
        owner = session.scalar(
            select(ZaloAccount).where(
                ZaloAccount.zalo_uid == candidate_uid,
                ZaloAccount.id != account.id,
            )
        )
        if owner is not None:
            raise AccountIdentityConflictError(
                f"UID Zalo này đang thuộc tài khoản quản lý '{owner.name}'. "
                "Hãy cập nhật phiên trên đúng tài khoản đó để giữ lịch sử liền mạch."
            )

    def test_account(self, session: Session, account: ZaloAccount) -> dict:
        try:
            client = self.client_for(account)
            info = client.fetchAccountInfo()
            payload = jsonable(info)
            candidate_uid = self.identity_from_payload(payload, client.uid)
            self.validate_identity(session, account, candidate_uid)
            account.zalo_uid = candidate_uid
            account.status = "online"
            account.last_seen_at = utcnow()
            account.last_error = None
            account.circuit_failures = 0
            account.circuit_open_until = None
            session.commit()
            return payload
        except AccountIdentityError as exc:
            account.status = "identity_mismatch"
            account.listener_enabled = False
            account.last_error = str(exc)[:4000]
            account.circuit_open_until = None
            session.commit()
            raise
        except Exception as exc:
            self._record_failure(
                session,
                account,
                exc,
                force_session_failure=True,
            )
            raise

    def execute(self, session: Session, account: ZaloAccount, action: str, payload: dict) -> Any:
        spec = get_action(action)
        if spec is None:
            raise UnsupportedActionError(f"Unknown action: {action}")
        if spec.status == "unsupported":
            raise UnsupportedActionError(spec.description or f"{action} is not supported")
        if not account.enabled:
            raise RuntimeError("Zalo account is disabled")
        if account.circuit_open_until and not is_expired(account.circuit_open_until):
            raise CircuitOpenError(f"Circuit is open until {account.circuit_open_until.isoformat()}")

        _limiter(account.id).wait()
        arguments = self._normalize_arguments(
            session,
            spec,
            {
                key: value
                for key, value in payload.items()
                if not key.startswith("_")
            },
        )
        try:
            client = self.client_for(account)
            result = getattr(client, spec.callable_method)(**arguments)
            serialized = jsonable(result)
            if action in {
                "sendFriendRequest",
                "acceptFriendRequest",
                "blockUser",
                "unblockUser",
            }:
                serialized = ensure_zalo_success(serialized, action)
            if action == "fetchAllFriends":
                cache_profiles(
                    session,
                    account_id=account.id,
                    result=serialized,
                    source="fetchAllFriends",
                )
            account.status = "online"
            account.last_seen_at = utcnow()
            account.last_error = None
            account.circuit_failures = 0
            account.circuit_open_until = None
            session.commit()
            return serialized
        except Exception as exc:
            self._record_failure(session, account, exc)
            raise

    def fetch_chat_group_info(
        self,
        session: Session,
        account: ZaloAccount,
        payload: dict,
    ) -> dict:
        if not account.enabled:
            raise RuntimeError("Zalo account is disabled")
        if account.circuit_open_until and not is_expired(account.circuit_open_until):
            raise CircuitOpenError(
                f"Circuit is open until {account.circuit_open_until.isoformat()}"
            )

        thread_id = _snapshot_text(payload.get("thread_id") or payload.get("groupId"))
        if not thread_id:
            raise ValueError("Group sync requires a thread_id")

        _limiter(account.id).wait()
        try:
            client = self.client_for(account)
            group_response = ensure_zalo_success(
                client.fetchGroupInfo(thread_id),
                "fetchGroupInfo",
            )
            grid_map = (
                group_response.get("gridInfoMap")
                if isinstance(group_response, dict)
                else None
            )
            raw_group = (
                grid_map.get(thread_id)
                if isinstance(grid_map, dict)
                else None
            )
            if not isinstance(raw_group, dict) and isinstance(grid_map, dict):
                raw_group = next(
                    (item for item in grid_map.values() if isinstance(item, dict)),
                    None,
                )
            if not isinstance(raw_group, dict):
                raise ZaloResponseError(
                    "fetchGroupInfo did not return the requested group"
                )

            member_ids: list[str] = []
            seen_ids: set[str] = set()

            def add_member_id(value: Any) -> None:
                member_id = _snapshot_text(value)
                if member_id and member_id not in seen_ids:
                    member_ids.append(member_id)
                    seen_ids.add(member_id)

            values = raw_group.get("memberIds")
            using_member_versions = not isinstance(values, list)
            if using_member_versions:
                values = raw_group.get("memVerList")
            if isinstance(values, list):
                for member_id in values:
                    normalized_id = member_id
                    if using_member_versions and isinstance(member_id, str):
                        candidate, separator, version = member_id.rpartition("_")
                        if separator and candidate and version.isdigit():
                            normalized_id = candidate
                    add_member_id(normalized_id)
            for item in raw_group.get("currentMems") or []:
                if isinstance(item, dict):
                    add_member_id(item.get("id") or item.get("userId"))
            owner_id = _snapshot_text(
                raw_group.get("creatorId") or raw_group.get("ownerId")
            )
            add_member_id(owner_id)
            admin_ids = []
            for admin_id in raw_group.get("adminIds") or []:
                normalized = _snapshot_text(admin_id)
                if normalized:
                    admin_ids.append(normalized)
                    add_member_id(normalized)

            profiles: dict[str, dict] = {}
            for item in raw_group.get("currentMems") or []:
                if not isinstance(item, dict):
                    continue
                profile = _compact_member_profile(item)
                if profile:
                    profiles[profile["user_id"]] = profile

            for offset in range(0, min(len(member_ids), 2000), 200):
                chunk = member_ids[offset : offset + 200]
                profile_response = ensure_zalo_success(
                    client.fetchUserInfo(list(chunk)),
                    "fetchUserInfo",
                )
                for fallback_id, item in _profile_records(profile_response):
                    profile = _compact_member_profile(item, fallback_id)
                    if profile:
                        profile["user_id"] = canonical_profile_user_id(
                            profile["user_id"],
                            chunk,
                        )
                        profiles[profile["user_id"]] = profile

            members = []
            admin_set = set(admin_ids)
            for member_id in member_ids[:2000]:
                profile = profiles.get(member_id) or {
                    "user_id": member_id,
                    "name": f"Zalo {member_id[-6:]}",
                    "avatar_url": None,
                    "online": None,
                }
                members.append(
                    {
                        **profile,
                        "role": (
                            "owner"
                            if member_id == owner_id
                            else "admin"
                            if member_id in admin_set
                            else "member"
                        ),
                    }
                )

            role_order = {"owner": 0, "admin": 1, "member": 2}
            members.sort(
                key=lambda item: (
                    role_order.get(item["role"], 3),
                    str(item["name"]).casefold(),
                )
            )
            result = {
                "thread_id": thread_id,
                "group": {
                    "thread_id": thread_id,
                    "name": _snapshot_text(raw_group.get("name"))
                    or f"Nhóm Zalo {thread_id[-6:]}",
                    "description": _snapshot_text(raw_group.get("desc")),
                    "avatar_url": _snapshot_text(
                        raw_group.get("fullAvt") or raw_group.get("avt")
                    ),
                    "member_count": raw_group.get("totalMember") or len(member_ids),
                    "owner_id": owner_id,
                    "admin_ids": admin_ids,
                    "has_more_members": bool(raw_group.get("hasMoreMember")),
                },
                "members": members,
            }
            cache_profiles(
                session,
                account_id=account.id,
                result=members,
                source="fetchGroupInfo",
                requested_user_ids=member_ids,
            )
            self._record_success(session, account)
            return result
        except Exception as exc:
            self._record_failure(session, account, exc)
            raise

    def fetch_chat_profiles(
        self,
        session: Session,
        account: ZaloAccount,
        payload: dict,
    ) -> dict:
        if not account.enabled:
            raise RuntimeError("Zalo account is disabled")
        if account.circuit_open_until and not is_expired(account.circuit_open_until):
            raise CircuitOpenError(
                f"Circuit is open until {account.circuit_open_until.isoformat()}"
            )
        user_ids: list[str] = []
        seen: set[str] = set()
        for value in payload.get("user_ids") or []:
            user_id = _snapshot_text(value)
            if (
                user_id
                and user_id != account.zalo_uid
                and user_id not in seen
                and len(user_id) <= 100
            ):
                user_ids.append(user_id)
                seen.add(user_id)
        if not user_ids:
            raise ValueError("Profile sync requires at least one user ID")
        if len(user_ids) > 100:
            raise ValueError("Profile sync is limited to 100 users")

        _limiter(account.id).wait()
        try:
            client = self.client_for(account)
            response = ensure_zalo_success(
                client.fetchUserInfo(list(user_ids)),
                "fetchUserInfo",
            )
            profiles = cache_profiles(
                session,
                account_id=account.id,
                result=response,
                source="fetchUserInfo",
                requested_user_ids=user_ids,
            )
            self._record_success(session, account)
            return {
                "requested_user_ids": user_ids,
                "profiles": profiles,
                "count": len(profiles),
            }
        except Exception as exc:
            self._record_failure(session, account, exc)
            raise

    def fetch_chat_group_pins(
        self,
        session: Session,
        account: ZaloAccount,
        payload: dict,
    ) -> dict:
        if not account.enabled:
            raise RuntimeError("Zalo account is disabled")
        if account.circuit_open_until and not is_expired(account.circuit_open_until):
            raise CircuitOpenError(
                f"Circuit is open until {account.circuit_open_until.isoformat()}"
            )

        thread_id = _snapshot_text(payload.get("thread_id") or payload.get("groupId"))
        if not thread_id:
            raise ValueError("Group pin sync requires a thread_id")

        _limiter(account.id).wait()
        try:
            client = self.client_for(account)
            response = ensure_zalo_success(
                client.getGroupPinMsg(thread_id, page=1, count=50),
                "getGroupPinMsg",
            )
            pins = _group_pin_records(response)
            self._record_success(session, account)
            return {
                "thread_id": thread_id,
                "pins": pins,
                "count": len(pins),
                "limit": 50,
            }
        except Exception as exc:
            self._record_failure(session, account, exc)
            raise

    def fetch_chat_group_notes(
        self,
        session: Session,
        account: ZaloAccount,
        payload: dict,
    ) -> dict:
        if not account.enabled:
            raise RuntimeError("Zalo account is disabled")
        if account.circuit_open_until and not is_expired(account.circuit_open_until):
            raise CircuitOpenError(
                f"Circuit is open until {account.circuit_open_until.isoformat()}"
            )

        thread_id = _snapshot_text(payload.get("thread_id") or payload.get("groupId"))
        if not thread_id:
            raise ValueError("Group note sync requires a thread_id")

        _limiter(account.id).wait()
        try:
            client = self.client_for(account)
            response = ensure_zalo_success(
                client.getGroupNote(thread_id, page=1, count=50),
                "getGroupNote",
            )
            notes = _group_note_records(response)
            self._record_success(session, account)
            return {
                "thread_id": thread_id,
                "notes": notes,
                "count": len(notes),
                "limit": 50,
            }
        except Exception as exc:
            self._record_failure(session, account, exc)
            raise

    def fetch_chat_group_polls(
        self,
        session: Session,
        account: ZaloAccount,
        payload: dict,
    ) -> dict:
        if not account.enabled:
            raise RuntimeError("Zalo account is disabled")
        if account.circuit_open_until and not is_expired(account.circuit_open_until):
            raise CircuitOpenError(
                f"Circuit is open until {account.circuit_open_until.isoformat()}"
            )

        thread_id = _snapshot_text(payload.get("thread_id") or payload.get("groupId"))
        if not thread_id:
            raise ValueError("Group poll sync requires a thread_id")

        _limiter(account.id).wait()
        try:
            client = self.client_for(account)
            response = ensure_zalo_success(
                client.getGroupPoll(thread_id, page=1, count=50),
                "getGroupPoll",
            )
            polls = _group_poll_records(response)
            self._record_success(session, account)
            return {
                "thread_id": thread_id,
                "polls": polls,
                "count": len(polls),
                "limit": 50,
            }
        except Exception as exc:
            self._record_failure(session, account, exc)
            raise

    def fetch_chat_group_poll_detail(
        self,
        session: Session,
        account: ZaloAccount,
        payload: dict,
    ) -> dict:
        if not account.enabled:
            raise RuntimeError("Zalo account is disabled")
        if account.circuit_open_until and not is_expired(account.circuit_open_until):
            raise CircuitOpenError(
                f"Circuit is open until {account.circuit_open_until.isoformat()}"
            )

        thread_id = _snapshot_text(payload.get("thread_id") or payload.get("groupId"))
        poll_id = _snapshot_text(payload.get("poll_id") or payload.get("pollId"))
        if not thread_id or not poll_id:
            raise ValueError("Poll detail requires thread_id and poll_id")

        _limiter(account.id).wait()
        try:
            client = self.client_for(account)
            response = ensure_zalo_success(
                client.viewPollDetail(poll_id),
                "viewPollDetail",
            )
            polls = _group_poll_records(response)
            poll = next(
                (item for item in polls if item.get("poll_id") == poll_id),
                polls[0] if polls else None,
            )
            self._record_success(session, account)
            return {
                "thread_id": thread_id,
                "poll_id": poll_id,
                "poll": poll,
            }
        except Exception as exc:
            self._record_failure(session, account, exc)
            raise

    def react_to_chat_message(
        self,
        session: Session,
        account: ZaloAccount,
        payload: dict,
    ) -> Any:
        if not account.enabled:
            raise RuntimeError("Zalo account is disabled")
        thread_type = self._thread_type(payload.get("thread_type"))
        message_object = payload.get("messageObject")
        if not isinstance(message_object, dict):
            raise ValueError("Reaction requires a source message object")
        context = payload.get("_chat_context")
        context = context if isinstance(context, dict) else {}
        try:
            repeat_count = int(payload.get("repeatCount") or 1)
        except (TypeError, ValueError):
            repeat_count = 1
        repeat_count = min(max(repeat_count, 1), 5)
        results = []
        try:
            client = self.client_for(account)
            source_message = MessageObject.fromDict(message_object, None)
            for index in range(repeat_count):
                if index:
                    time.sleep(AUTOMATION_REACTION_INTERVAL_SECONDS)
                _limiter(account.id).wait()
                result = client.sendReaction(
                    source_message,
                    str(payload.get("reactionIcon") or ""),
                    str(payload.get("thread_id") or ""),
                    thread_type,
                    reactionType=int(payload.get("reactionType", 75)),
                    source=int(payload.get("source", 6)),
                )
                results.append(ensure_zalo_success(result, "Thả reaction"))
            self._record_success(session, account)
            return {
                "reaction": results[0] if repeat_count == 1 else results,
                "fallback": None,
                "requested_count": repeat_count,
                "sent_count": len(results),
            }
        except Exception as reaction_error:
            fallback = str(context.get("fallback") or "none")
            if not results and fallback == "message" and not context.get("remove"):
                result = client.sendMessage(
                    Message(text=str(context.get("glyph") or "✨")),
                    str(payload.get("thread_id") or ""),
                    thread_type,
                )
                self._record_success(session, account)
                return {
                    "reaction": None,
                    "fallback": "message",
                    "fallback_result": jsonable(result),
                    "reaction_error": str(reaction_error),
                }
            if not results and fallback == "local" and not context.get("remove"):
                return {
                    "reaction": None,
                    "fallback": "local",
                    "reaction_error": str(reaction_error),
                }
            self._record_failure(session, account, reaction_error)
            if results:
                raise RuntimeError(
                    f"Zalo chỉ nhận {len(results)}/{repeat_count} reaction"
                ) from reaction_error
            raise

    def forward_chat_message(
        self,
        session: Session,
        account: ZaloAccount,
        payload: dict,
    ) -> Any:
        if not account.enabled:
            raise RuntimeError("Zalo account is disabled")
        _limiter(account.id).wait()
        client = self.client_for(account)
        thread_id = str(payload.get("thread_id") or "")
        thread_type = self._thread_type(payload.get("thread_type"))
        kind = str(payload.get("kind") or "text")
        media = payload.get("media")
        media = media if isinstance(media, dict) else {}
        source_message = payload.get("source_message")
        source_message = source_message if isinstance(source_message, dict) else {}
        text_value = str(payload.get("text") or "").strip()
        temp_path: Path | None = None
        try:
            if kind == "image":
                image_url = str(media.get("url") or media.get("thumbnail_url") or "")
                temp_path = self._download_zalo_media(client, image_url, ".jpg")
                result = client.sendLocalImage(
                    str(temp_path),
                    thread_id,
                    thread_type,
                    width=int(media.get("width") or 2560),
                    height=int(media.get("height") or 2560),
                )
            elif kind == "video" and media.get("url"):
                result = client.sendRemoteVideo(
                    str(media["url"]),
                    str(media.get("thumbnail_url") or ""),
                    int(media.get("duration_ms") or 0),
                    thread_id,
                    thread_type,
                    width=int(media.get("width") or 1280),
                    height=int(media.get("height") or 720),
                )
            elif kind == "audio" and media.get("url"):
                result = client.sendRemoteVoice(
                    str(media["url"]),
                    thread_id,
                    thread_type,
                    fileSize=media.get("size"),
                )
            elif kind == "file" and media.get("url"):
                filename = str(media.get("filename") or "tep-dinh-kem")
                result = client.sendRemoteFile(
                    str(media["url"]),
                    thread_id,
                    thread_type,
                    fileName=filename,
                    fileSize=media.get("size"),
                    extension=str(media.get("extension") or Path(filename).suffix.lstrip(".") or "bin"),
                )
            elif kind == "sticker" and media.get("sticker_id") and media.get("category_id"):
                result = client.sendSticker(
                    int(source_message.get("type") or 1),
                    str(media["sticker_id"]),
                    str(media["category_id"]),
                    thread_id,
                    thread_type,
                )
            elif kind == "link" and media.get("url"):
                parsed = urlsplit(str(media["url"]))
                result = client.sendLink(
                    str(media["url"]),
                    str(media.get("title") or text_value or media["url"]),
                    thread_id,
                    thread_type,
                    thumbnailUrl=media.get("thumbnail_url"),
                    domainUrl=parsed.netloc,
                    desc=media.get("description"),
                )
            elif kind == "poll":
                raise UnsupportedActionError("Forwarding polls is not supported")
            else:
                result = client.sendMessage(
                    Message(text=text_value or "[Nội dung được chia sẻ]"),
                    thread_id,
                    thread_type,
                )
            self._record_success(session, account)
            return jsonable(result)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def send_chat_media(
        self,
        session: Session,
        account: ZaloAccount,
        payload: dict,
    ) -> Any:
        if not account.enabled:
            raise RuntimeError("Zalo account is disabled")
        thread_id = str(payload.get("thread_id") or "")
        if not thread_id:
            raise ValueError("Media job requires a thread ID")
        thread_type = self._thread_type(payload.get("thread_type"))
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("Media job requires at least one upload")

        resolved: list[tuple[dict, MediaUpload, Path]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Media item is invalid")
            upload, path = self._resolve_upload_record(
                session,
                str(item.get("upload_token") or ""),
            )
            kind = str(item.get("kind") or "")
            validate_media_kind(upload.original_name, upload.content_type, kind)
            resolved.append((item, upload, path))

        client = self.client_for(account)
        text_value = str(payload.get("text") or "").strip()
        reply_payload = payload.get("replyMsg")
        reply_payload = reply_payload if isinstance(reply_payload, dict) else None
        all_images = all(item.get("kind") == "image" for item, _, _ in resolved)
        native_caption = bool(text_value and not reply_payload and all_images)
        results: list[dict] = []

        if all_images and len(resolved) > 1:
            _limiter(account.id).wait()
            first = resolved[0][0]
            result = client.sendMultiLocalImage(
                [str(path) for _, _, path in resolved],
                thread_id,
                thread_type,
                width=int(first.get("width") or 2560),
                height=int(first.get("height") or 2560),
                message=Message(text=text_value) if native_caption else None,
            )
            results.append(
                {
                    "kind": "image",
                    "count": len(resolved),
                    "result": ensure_zalo_success(result, "Gửi hình ảnh"),
                }
            )
        else:
            for item, upload, path in resolved:
                kind = str(item["kind"])
                _limiter(account.id).wait()
                video_metadata: VideoMetadata | None = None
                if kind == "image":
                    result = client.sendLocalImage(
                        str(path),
                        thread_id,
                        thread_type,
                        width=int(item.get("width") or 2560),
                        height=int(item.get("height") or 2560),
                        message=Message(text=text_value) if native_caption else None,
                    )
                elif kind == "gif":
                    result, gif_metadata = self._send_local_gif(
                        client,
                        path,
                        outbound_media_url(self.settings, upload.id),
                        thread_id,
                        thread_type,
                        gif_name=Path(upload.original_name).name or "sticker.gif",
                        width=int(item.get("width") or 500),
                        height=int(item.get("height") or 500),
                    )
                elif kind == "video":
                    video_metadata, thumbnail = self._prepare_video_upload(
                        session,
                        upload,
                        path,
                    )
                    result = client.sendRemoteVideo(
                        outbound_media_url(self.settings, upload.id),
                        outbound_media_url(self.settings, thumbnail.id),
                        video_metadata.duration_ms,
                        thread_id,
                        thread_type,
                        width=video_metadata.width,
                        height=video_metadata.height,
                        message=(
                            Message(text=text_value)
                            if text_value and not reply_payload and len(resolved) == 1
                            else None
                        ),
                    )
                elif kind == "audio":
                    result = client.sendRemoteVoice(
                        outbound_media_url(self.settings, upload.id),
                        thread_id,
                        thread_type,
                        fileSize=upload.size_bytes,
                    )
                else:
                    filename = Path(upload.original_name).name or "tep-dinh-kem"
                    result = client.sendRemoteFile(
                        outbound_media_url(self.settings, upload.id),
                        thread_id,
                        thread_type,
                        fileName=filename,
                        fileSize=upload.size_bytes,
                        extension=Path(filename).suffix.lstrip(".") or "bin",
                    )
                response = ensure_zalo_success(result, f"Gửi {kind}")
                result_item = {"kind": kind, "count": 1, "result": response}
                if kind == "gif":
                    result_item["metadata"] = gif_metadata
                if video_metadata is not None:
                    result_item["metadata"] = {
                        "width": video_metadata.width,
                        "height": video_metadata.height,
                        "duration_ms": video_metadata.duration_ms,
                        "codec": video_metadata.codec_name,
                    }
                results.append(result_item)

        caption_was_sent = native_caption or (
            len(resolved) == 1 and resolved[0][0].get("kind") == "video" and text_value and not reply_payload
        )
        if text_value and not caption_was_sent:
            _limiter(account.id).wait()
            if reply_payload:
                caption_result = client.replyMessage(
                    Message(text=text_value),
                    MessageObject.fromDict(reply_payload, None),
                    thread_id,
                    thread_type,
                )
            else:
                caption_result = client.sendMessage(
                    Message(text=text_value),
                    thread_id,
                    thread_type,
                )
            results.append(
                {
                    "kind": "caption",
                    "count": 1,
                    "result": ensure_zalo_success(caption_result, "Gửi chú thích"),
                }
            )

        self._record_success(session, account)
        return {"items": results, "count": len(resolved)}

    def send_chat_rich_media(
        self,
        session: Session,
        account: ZaloAccount,
        payload: dict,
    ) -> dict:
        """Send a sticker, GIF, link, or business card from the chat composer."""
        if not account.enabled:
            raise RuntimeError("Zalo account is disabled")
        thread_id = str(payload.get("thread_id") or "")
        if not thread_id:
            raise ValueError("Rich media job requires a thread ID")
        kind = str(payload.get("kind") or "")
        media = payload.get("media") if isinstance(payload.get("media"), dict) else {}
        text_value = str(payload.get("text") or "").strip()
        thread_type = self._thread_type(payload.get("thread_type"))
        try:
            client = self.client_for(account)
            _limiter(account.id).wait()
            if kind == "sticker":
                sticker_id = int(media.get("sticker_id") or 0)
                category_id = int(media.get("category_id") or 0)
                if sticker_id <= 0 or category_id <= 0:
                    raise ValueError("Sticker cần sticker_id và category_id hợp lệ")
                result = client.sendSticker(
                    int(media.get("sticker_type") or 1),
                    sticker_id,
                    category_id,
                    thread_id,
                    thread_type,
                )
            elif kind == "gif":
                library_id = str(media.get("library_gif_id") or "")
                if library_id:
                    library_gif = session.get(GifLibraryItem, library_id)
                    if library_gif is None:
                        raise ValueError("GIF không còn trong thư viện")
                    path = Path(library_gif.stored_path)
                    if (
                        not safe_child_path(self.settings.gif_library_dir, path)
                        or not path.is_file()
                    ):
                        raise ValueError("Tệp GIF trong thư viện không còn tồn tại")
                    thumbnail_url = str(
                        media.get("thumbnail_url")
                        or gif_library_media_url(self.settings, library_gif.id)
                    )
                    gif_name = Path(library_gif.original_name).name or "sticker.gif"
                else:
                    upload, path = self._resolve_upload_record(
                        session,
                        str(media.get("upload_token") or ""),
                    )
                    if upload.original_name.lower().rsplit(".", 1)[-1] != "gif":
                        raise ValueError("GIF cần tệp .gif hợp lệ")
                    thumbnail_url = str(
                        media.get("thumbnail_url")
                        or outbound_media_url(self.settings, upload.id)
                    )
                    gif_name = Path(upload.original_name).name or "sticker.gif"
                result, gif_metadata = self._send_local_gif(
                    client,
                    path,
                    thumbnail_url,
                    thread_id,
                    thread_type,
                    gif_name=gif_name,
                    width=int(media.get("width") or 500),
                    height=int(media.get("height") or 500),
                )
            elif kind == "link":
                url = str(media.get("url") or "").strip()
                parsed = urlsplit(url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError("Liên kết phải là URL http(s) hợp lệ")
                result = client.sendLink(
                    url,
                    str(media.get("title") or url),
                    thread_id,
                    thread_type,
                    thumbnailUrl=media.get("thumbnail_url"),
                    domainUrl=parsed.netloc,
                    desc=media.get("description"),
                    message=Message(text=text_value) if text_value else None,
                )
            elif kind == "contact":
                user_id = str(media.get("contact_user_id") or "").strip()
                if not user_id:
                    raise ValueError("Danh thiếp cần contact_user_id")
                result = client.sendBusinessCard(
                    user_id,
                    str(media.get("contact_qr_url") or ""),
                    thread_id,
                    thread_type,
                    phone=media.get("contact_phone"),
                )
            else:
                raise ValueError(f"Rich media không được hỗ trợ: {kind or 'trống'}")
            operation = {
                "sticker": "Gửi sticker",
                "gif": "Gửi GIF",
                "link": "Gửi liên kết",
                "contact": "Gửi danh thiếp",
            }.get(kind, "Gửi nội dung")
            result = ensure_zalo_success(result, operation)
            self._record_success(session, account)
            response = {"kind": kind, "result": result}
            if kind == "gif":
                response["metadata"] = gif_metadata
            return response
        except Exception as exc:
            self._record_failure(session, account, exc)
            raise

    def send_chat_automation_sticker(
        self,
        session: Session,
        account: ZaloAccount,
        payload: dict,
    ) -> dict:
        """Reply to a source group message with the configured sticker image."""
        if not account.enabled:
            raise RuntimeError("Zalo account is disabled")
        thread_id = str(payload.get("thread_id") or "").strip()
        source_message_id = str(payload.get("reply_message_id") or "").strip()
        media = payload.get("media") if isinstance(payload.get("media"), dict) else {}
        thumbnail_url = str(media.get("thumbnail_url") or "").strip()
        parsed = urlsplit(thumbnail_url)
        if not thread_id or not source_message_id:
            raise ValueError("Automation sticker requires a group and source message")
        if self._thread_type(payload.get("thread_type")) != ThreadType.GROUP:
            raise ValueError("Mention sticker automation only supports groups")
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Automation sticker URL must use HTTPS")

        try:
            client = self.client_for(account)
            _limiter(account.id).wait()
            result = client.sendCustomSticker(
                thumbnail_url,
                thumbnail_url,
                thread_id,
                ThreadType.GROUP,
                reply=source_message_id,
                width=int(media.get("width") or 130),
                height=int(media.get("height") or 130),
            )
            result = ensure_zalo_success(result, "Tự động trả lời sticker")
            self._record_success(session, account)
            return {
                "kind": "sticker",
                "automation": True,
                "reply_message_id": source_message_id,
                "result": result,
            }
        except Exception as exc:
            self._record_failure(session, account, exc)
            raise

    def _prepare_video_upload(
        self,
        session: Session,
        upload: MediaUpload,
        path: Path,
    ) -> tuple[VideoMetadata, MediaUpload]:
        metadata = probe_video(
            path,
            ffprobe_path=self.settings.ffprobe_path,
            timeout_seconds=self.settings.media_process_timeout_seconds,
        )
        thumbnail_id = secrets.token_hex(16)
        thumbnail_path = self.settings.upload_dir / f"{thumbnail_id}.jpg"
        generate_video_thumbnail(
            path,
            thumbnail_path,
            metadata,
            ffmpeg_path=self.settings.ffmpeg_path,
            timeout_seconds=self.settings.media_process_timeout_seconds,
        )
        thumbnail = MediaUpload(
            id=thumbnail_id,
            original_name=f"{Path(upload.original_name).stem[:220]}-thumbnail.jpg",
            stored_path=str(thumbnail_path),
            size_bytes=thumbnail_path.stat().st_size,
            content_type="image/jpeg",
            created_by_id=upload.created_by_id,
            expires_at=upload.expires_at,
        )
        try:
            session.add(thumbnail)
            # Zalo fetches this URL while sendRemoteVideo is running, so the web
            # service must be able to read the row from its separate DB session.
            session.commit()
        except Exception:
            session.rollback()
            thumbnail_path.unlink(missing_ok=True)
            raise
        return metadata, thumbnail

    def _send_local_gif(
        self,
        client: ZaloAPI,
        path: Path,
        thumbnail_url: str,
        thread_id: str,
        thread_type: ThreadType,
        *,
        gif_name: str,
        width: int,
        height: int,
    ) -> tuple[Any, dict[str, Any]]:
        get_limits = getattr(client, "getGifUploadLimits", None)
        raw_limits = get_limits() if callable(get_limits) else {}
        raw_limits = raw_limits if isinstance(raw_limits, dict) else {}
        max_bytes = max(256 * 1024, int(raw_limits.get("max_bytes") or 3 * 1024 * 1024))
        max_width = max(1, int(raw_limits.get("max_width") or 640))
        max_height = max(1, int(raw_limits.get("max_height") or 480))
        prepared: PreparedGif = prepare_gif_for_zalo(
            path,
            self.settings.upload_dir,
            max_bytes=max_bytes,
            max_width=max_width,
            max_height=max_height,
            supplied_width=width,
            supplied_height=height,
            ffmpeg_path=self.settings.ffmpeg_path,
            ffprobe_path=self.settings.ffprobe_path,
            timeout_seconds=self.settings.media_process_timeout_seconds,
        )
        try:
            result = client.sendLocalGif(
                str(prepared.path),
                thumbnail_url,
                thread_id,
                thread_type,
                gifName=gif_name,
                width=prepared.metadata.width,
                height=prepared.metadata.height,
            )
            return result, {
                "width": prepared.metadata.width,
                "height": prepared.metadata.height,
                "size_bytes": prepared.metadata.size_bytes,
                "original_size_bytes": prepared.original_size_bytes,
                "optimized": prepared.optimized,
                "limit_bytes": max_bytes,
            }
        finally:
            if prepared.optimized:
                prepared.path.unlink(missing_ok=True)

    @staticmethod
    def _thread_type(value: Any) -> ThreadType:
        return (
            ThreadType.GROUP
            if "group" in str(value or "").lower()
            else ThreadType.USER
        )

    def _record_success(self, session: Session, account: ZaloAccount) -> None:
        account.status = "online"
        account.last_seen_at = utcnow()
        account.last_error = None
        account.circuit_failures = 0
        account.circuit_open_until = None
        session.commit()

    def _download_zalo_media(
        self,
        client: ZaloAPI,
        url: str,
        fallback_suffix: str,
    ) -> Path:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        allowed_suffixes = ("zalo.me", "zaloapp.com", "zadn.vn", "zdn.vn")
        if (
            parsed.scheme != "https"
            or not hostname
            or not any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in allowed_suffixes)
        ):
            raise ValueError("Media URL is not an approved Zalo CDN URL")
        suffix = Path(parsed.path).suffix[:10] or fallback_suffix
        self.settings.upload_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=suffix,
            prefix="forward-",
            dir=self.settings.upload_dir,
            delete=False,
        ) as handle:
            path = Path(handle.name)
            total = 0
            limit = self.settings.max_upload_mb * 1024 * 1024
            try:
                with client._state._session.get(url, stream=True, timeout=30) as response:
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=128 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > limit:
                            raise ValueError("Media exceeds the configured upload limit")
                        handle.write(chunk)
            except Exception:
                path.unlink(missing_ok=True)
                raise
        return path

    def _record_failure(
        self,
        session: Session,
        account: ZaloAccount,
        exc: Exception,
        *,
        force_session_failure: bool = False,
    ) -> None:
        account.circuit_failures += 1
        session_failure = force_session_failure or isinstance(
            exc,
            SESSION_FAILURE_TYPES,
        )
        if session_failure:
            account.status = "error"
            account.listener_enabled = False
            account.last_error = str(exc)[:4000]
        if account.circuit_failures >= 5:
            account.circuit_open_until = utcnow() + timedelta(minutes=5)
        session.commit()

    def _normalize_arguments(
        self, session: Session, spec: ActionSpec, payload: dict
    ) -> dict[str, Any]:
        arguments = dict(payload)
        if "thread_type" in arguments:
            value = str(arguments["thread_type"]).lower()
            arguments["thread_type"] = (
                ThreadType.GROUP if value in {"group", "threadtype.group"} else ThreadType.USER
            )
        if "message" in arguments and isinstance(arguments["message"], (str, dict)):
            message = arguments["message"]
            arguments["message"] = Message(**message) if isinstance(message, dict) else Message(text=message)
        for name in ("messageObject", "replyMsg", "pinMsg"):
            if isinstance(arguments.get(name), dict):
                arguments[name] = MessageObject.fromDict(arguments[name], None)
        for name in ("imagePath", "gifPath", "filePath"):
            if name in arguments:
                arguments[name] = str(self._resolve_upload(session, str(arguments[name])))
        if "imagePathList" in arguments:
            arguments["imagePathList"] = [
                str(self._resolve_upload(session, str(token)))
                for token in arguments["imagePathList"]
            ]
        return arguments

    def _resolve_upload(self, session: Session, token: str) -> Path:
        _, path = self._resolve_upload_record(session, token)
        return path

    def _resolve_upload_record(
        self,
        session: Session,
        token: str,
    ) -> tuple[MediaUpload, Path]:
        if not token.startswith("upload:"):
            raise ValueError("Local files must use an upload:<id> token")
        upload = session.get(MediaUpload, token.removeprefix("upload:"))
        if upload is None or is_expired(upload.expires_at):
            raise ValueError("Upload token is missing or expired")
        path = Path(upload.stored_path)
        if not safe_child_path(self.settings.upload_dir, path) or not path.is_file():
            raise ValueError("Upload path is invalid")
        return upload, path
