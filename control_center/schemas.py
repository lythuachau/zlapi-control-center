from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

RoleName = Literal["super_admin", "admin", "operator", "viewer"]


class AccountCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    imei: str = Field(min_length=5, max_length=255)
    cookies: dict[str, str]
    user_agent: str | None = Field(default=None, max_length=1000)
    listener_enabled: bool = False

    @field_validator("cookies")
    @classmethod
    def cookies_not_empty(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("Cookie object cannot be empty")
        return value


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    imei: str | None = Field(default=None, min_length=5, max_length=255)
    cookies: dict[str, str] | None = None
    user_agent: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None
    listener_enabled: bool | None = None


class AccountDelete(BaseModel):
    confirmation: str = Field(min_length=1, max_length=120)


class ActionInvoke(BaseModel):
    account_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    confirmation: str | None = None
    priority: int = Field(default=100, ge=1, le=1000)


class ChatMessageCreate(BaseModel):
    account_id: str
    thread_id: str = Field(min_length=1, max_length=100)
    thread_type: Literal["user", "group"] = "user"
    text: str = Field(min_length=1, max_length=4000)
    reply_event_id: int | None = Field(default=None, ge=1)

    @field_validator("text")
    @classmethod
    def message_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be blank")
        return value


class ChatMediaItem(BaseModel):
    upload_token: str = Field(pattern=r"^upload:[a-f0-9]{32}$")
    kind: Literal["image", "video", "audio", "file"]
    name: str = Field(min_length=1, max_length=255)
    content_type: str | None = Field(default=None, max_length=120)
    size_bytes: int = Field(ge=1)
    width: int | None = Field(default=None, ge=1, le=20_000)
    height: int | None = Field(default=None, ge=1, le=20_000)
    duration_ms: int | None = Field(default=None, ge=0, le=86_400_000)


class ChatMediaCreate(BaseModel):
    account_id: str
    thread_id: str = Field(min_length=1, max_length=100)
    thread_type: Literal["user", "group"] = "user"
    text: str = Field(default="", max_length=4000)
    reply_event_id: int | None = Field(default=None, ge=1)
    items: list[ChatMediaItem] = Field(min_length=1, max_length=10)

    @field_validator("text")
    @classmethod
    def media_caption_trimmed(cls, value: str) -> str:
        return value.strip()


class ChatReadStateUpdate(BaseModel):
    account_id: str
    thread_id: str = Field(min_length=1, max_length=100)


class ChatGroupRenameCreate(BaseModel):
    account_id: str
    current_name: str = Field(min_length=1, max_length=120)
    new_name: str = Field(min_length=1, max_length=120)

    @field_validator("current_name", "new_name")
    @classmethod
    def group_names_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Group name cannot be blank")
        return value


class ChatGroupInviteCreate(BaseModel):
    account_id: str
    user_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("user_ids")
    @classmethod
    def unique_member_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            member_id = str(item).strip()
            if not member_id or len(member_id) > 100:
                raise ValueError("Member ID is invalid")
            if member_id not in seen:
                normalized.append(member_id)
                seen.add(member_id)
        if not normalized:
            raise ValueError("At least one member ID is required")
        return normalized


class ChatReactionCreate(BaseModel):
    account_id: str
    event_id: int = Field(ge=1)
    reaction_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    remove: bool = False


class ChatForwardTarget(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    thread_type: Literal["user", "group"] = "user"
    name: str | None = Field(default=None, max_length=160)


class ChatForwardCreate(BaseModel):
    account_id: str
    event_id: int = Field(ge=1)
    targets: list[ChatForwardTarget] = Field(min_length=1, max_length=20)

    @field_validator("targets")
    @classmethod
    def unique_targets(cls, value: list[ChatForwardTarget]) -> list[ChatForwardTarget]:
        seen: set[tuple[str, str]] = set()
        result = []
        for item in value:
            identity = (item.thread_id, item.thread_type)
            if identity not in seen:
                seen.add(identity)
                result.append(item)
        return result


class ChatUndoCreate(BaseModel):
    account_id: str
    event_id: int = Field(ge=1)


class ReactionCatalogItem(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    label: str = Field(min_length=1, max_length=60)
    glyph: str = Field(min_length=1, max_length=16)
    zalo_icon: str = Field(default="", max_length=32)
    r_type: int = Field(default=-1, ge=-1, le=10_000)
    source: int = Field(default=6, ge=0, le=100)
    mode: Literal["native", "experimental", "local"] = "experimental"
    quick: bool = False
    enabled: bool = True
    verified: bool = False
    fallback: Literal["none", "message", "local"] = "none"
    locked: bool = False


class ReactionCatalogUpdate(BaseModel):
    items: list[ReactionCatalogItem] = Field(min_length=1, max_length=80)


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{3,64}$")
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=12, max_length=256)
    role: RoleName = "viewer"


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=120)
    password: str | None = Field(default=None, min_length=12, max_length=256)
    role: RoleName | None = None
    active: bool | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)

    @field_validator("new_password")
    @classmethod
    def new_password_is_strong(cls, value: str) -> str:
        category_count = sum(
            (
                any(character.islower() for character in value),
                any(character.isupper() for character in value),
                any(character.isdigit() for character in value),
                any(not character.isalnum() for character in value),
            )
        )
        if category_count < 3:
            raise ValueError(
                "Mật khẩu mới phải có ít nhất 3 nhóm: chữ thường, chữ hoa, số, ký tự đặc biệt."
            )
        return value


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    expires_at: datetime | None = None


class SettingUpdate(BaseModel):
    value: Any
