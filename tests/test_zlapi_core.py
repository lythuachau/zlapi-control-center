from __future__ import annotations

from types import SimpleNamespace

import pytest

from control_center.catalog import catalog_payload
from zlapi import MessageReaction
from zlapi import _state as state_module
from zlapi._exception import ZaloLoginError
from zlapi._state import State


def test_message_reaction_uses_normalized_message_type():
    message = SimpleNamespace(msgId="101", cliMsgId="202", msgType="webchat")
    reaction = MessageReaction(message)
    assert reaction == [{"gMsgID": 101, "cMsgID": 202, "msgType": 1}]


def test_zalo_states_do_not_share_mutable_headers():
    first = State()
    second = State()
    first._headers["User-Agent"] = "first-account"
    assert second._headers["User-Agent"] != "first-account"


def test_zalo_login_preserves_rejected_session_error_code(monkeypatch):
    class RejectedSessionResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": None,
                "error_code": 102,
                "error_message": "The session key has reached its timeout",
            }

    monkeypatch.setattr(
        state_module.requests,
        "get",
        lambda *_args, **_kwargs: RejectedSessionResponse(),
    )
    state = State()
    state.set_cookies({"zpw_sek": "expired"})

    with pytest.raises(ZaloLoginError, match=r"Error #102"):
        state.login("cookie-session", "cookie-session", "test-imei")


def test_catalog_preserves_array_and_object_defaults():
    catalog = {action["name"]: action for action in catalog_payload()}
    create_group = {field["name"]: field for field in catalog["createGroup"]["fields"]}
    assert create_group["members"]["default"] == []
    assert create_group["members"]["type"] == "array"
    account_setting = {
        field["name"]: field for field in catalog["changeAccountSetting"]["fields"]
    }
    assert account_setting["biz"]["default"] == {}
    assert account_setting["biz"]["type"] == "object"
