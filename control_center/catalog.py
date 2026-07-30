from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from zlapi import ZaloAPI


@dataclass(frozen=True)
class ActionSpec:
    name: str
    category: str
    status: str = "ready"
    dangerous: bool = False
    description: str = ""
    method: str | None = None

    @property
    def callable_method(self) -> str:
        return self.method or self.name


_GROUPS: dict[str, tuple[str, ...]] = {
    "Tài khoản": (
        "fetchAccountInfo",
        "fetchPhoneNumber",
        "fetchUserInfo",
        "changeAccountSetting",
        "changeAccountAvatar",
    ),
    "Bạn bè": (
        "fetchAllFriends",
        "sendFriendRequest",
        "acceptFriendRequest",
        "blockViewFeed",
        "blockUser",
        "unblockUser",
        "sendReport",
    ),
    "Nhóm": (
        "fetchGroupInfo",
        "fetchAllGroups",
        "getRecentGroup",
        "createGroup",
        "changeGroupAvatar",
        "changeGroupName",
        "changeGroupDesc",
        "changeGroupSetting",
        "changeGroupOwner",
        "addUsersToGroup",
        "kickUsersInGroup",
        "blockUsersInGroup",
        "unblockUsersInGroup",
        "addGroupAdmins",
        "removeGroupAdmins",
        "viewGroupPending",
        "handleGroupPending",
        "disperseGroup",
    ),
    "Tin nhắn": (
        "getLastMsgs",
        "sendMessage",
        "replyMessage",
        "sendMentionMessage",
        "undoMessage",
        "sendReaction",
        "sendMultiReaction",
        "sendRemoteFile",
        "sendRemoteVideo",
        "sendRemoteVoice",
        "sendLocalImage",
        "sendMultiLocalImage",
        "sendLocalGif",
        "sendSticker",
        "sendCustomSticker",
        "sendLink",
        "sendBusinessCard",
        "setTyping",
        "markAsDelivered",
        "markAsRead",
    ),
    "Bảng tin nhóm": (
        "getGroupBoardList",
        "getGroupPinMsg",
        "getGroupNote",
        "getGroupPoll",
        "pinGroupMsg",
        "unpinGroupMsg",
        "deleteGroupMsg",
    ),
    "Bình chọn": ("viewPollDetail", "createPoll", "lockPoll"),
}

_DANGEROUS = {
    "blockUser",
    "sendReport",
    "changeAccountSetting",
    "changeAccountAvatar",
    "changeGroupAvatar",
    "changeGroupName",
    "addUsersToGroup",
    "changeGroupSetting",
    "changeGroupOwner",
    "kickUsersInGroup",
    "blockUsersInGroup",
    "removeGroupAdmins",
    "deleteGroupMsg",
    "handleGroupPending",
    "lockPoll",
    "disperseGroup",
}

_UNSTABLE = {
    "changeAccountAvatar",
    "sendReport",
    "pinGroupMsg",
    "sendCustomSticker",
    "markAsDelivered",
    "markAsRead",
}

_UNSUPPORTED = {
    "changeGroupDesc": "Thư viện gốc chưa cài đặt endpoint này.",
    "loginWithQr": "Thư viện gốc chưa hỗ trợ đăng nhập QR.",
    "votePoll": "Thư viện gốc chỉ xem, tạo và khóa poll; chưa hỗ trợ bỏ phiếu.",
    "assignTask": "Repository chưa có API giao việc.",
}


def _build_specs() -> dict[str, ActionSpec]:
    specs: dict[str, ActionSpec] = {}
    for category, actions in _GROUPS.items():
        for action in actions:
            status = "unsupported" if action in _UNSUPPORTED else "unstable" if action in _UNSTABLE else "ready"
            specs[action] = ActionSpec(
                name=action,
                category=category,
                status=status,
                dangerous=action in _DANGEROUS,
                description=_UNSUPPORTED.get(action, ""),
            )
    for action, description in _UNSUPPORTED.items():
        specs.setdefault(
            action,
            ActionSpec(
                name=action,
                category="Chưa hỗ trợ",
                status="unsupported",
                description=description,
            ),
        )
    return specs


ACTIONS = _build_specs()


def get_action(name: str) -> ActionSpec | None:
    return ACTIONS.get(name)


def _json_default(value: Any) -> Any:
    if value is inspect.Parameter.empty:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _json_default(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_default(item) for item in value]
    return getattr(value, "name", str(value))


def _field_type(name: str, default: Any) -> str:
    lowered = name.lower()
    if name in {"members", "user_ids", "imagePathList"}:
        return "array"
    if name in {"messageObject", "replyMsg", "pinMsg", "reactionObj", "biz", "custom_payload"}:
        return "object"
    if lowered.startswith(("is", "has")) or isinstance(default, bool):
        return "boolean"
    if isinstance(default, (int, float)) or lowered in {
        "ttl",
        "width",
        "height",
        "duration",
        "filesize",
        "reason",
        "page",
        "count",
    }:
        return "number"
    if name == "thread_type":
        return "thread_type"
    if name in {"imagePath", "gifPath", "filePath"}:
        return "upload"
    return "string"


def action_schema(spec: ActionSpec) -> dict:
    fields: list[dict] = []
    method = getattr(ZaloAPI, spec.callable_method, None)
    if method is not None:
        signature = inspect.signature(method)
        for parameter in signature.parameters.values():
            if parameter.name == "self" or parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            fields.append(
                {
                    "name": parameter.name,
                    "required": parameter.default is inspect.Parameter.empty,
                    "default": _json_default(parameter.default),
                    "type": _field_type(parameter.name, parameter.default),
                }
            )
    return {
        "name": spec.name,
        "category": spec.category,
        "status": spec.status,
        "dangerous": spec.dangerous,
        "description": spec.description,
        "fields": fields,
    }


def catalog_payload() -> list[dict]:
    return [action_schema(spec) for spec in ACTIONS.values()]
