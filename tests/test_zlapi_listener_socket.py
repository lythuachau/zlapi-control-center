from __future__ import annotations

import threading
from types import SimpleNamespace

import zlapi._client as client_module
from zlapi import ZaloAPI


class FakeWebSocket:
    def __init__(self, frames: list[bytes] | None = None):
        self.frames = list(frames or [])
        self.close_count = 0

    def recv(self):
        return self.frames.pop(0)

    def close(self):
        self.close_count += 1


def bare_client(ws: FakeWebSocket | None = None) -> ZaloAPI:
    client = ZaloAPI.__new__(ZaloAPI)
    client._condition = threading.Event()
    client._listening = False
    client.listening = False
    client._ws_lock = threading.Lock()
    client._active_ws = ws
    client._listen_stop = threading.Event()
    client.run_forever = False
    return client


def test_stop_listening_closes_active_websocket_only_once():
    websocket = FakeWebSocket()
    client = bare_client(websocket)

    client.stopListening()
    client.stopListening()

    assert websocket.close_count == 1
    assert client._condition.is_set()
    assert client._active_ws is None


def test_duplicate_websocket_event_reports_clear_error_and_closes_once(monkeypatch):
    websocket = FakeWebSocket([b"key!{\"key\":\"secret\"}", b"dupe{}"])
    client = bare_client()
    client._state = SimpleNamespace(
        _config={"zpw_ws": ["wss://example.test/ws"]},
        _headers={"User-Agent": "test-agent"},
        get_cookies=lambda: {"zpw_sek": "secret"},
    )
    errors: list[Exception] = []
    client.onListening = lambda: None
    client.onErrorCallBack = lambda error, ts=None: errors.append(error)

    monkeypatch.setattr(client_module, "connect", lambda *_args, **_kwargs: websocket)
    monkeypatch.setattr(
        client_module._util,
        "getHeader",
        lambda header: (1, 1, 1) if header == b"key!" else (1, 3000, 0),
    )
    monkeypatch.setattr(client_module._util, "zws_decode", lambda *_args: {})
    monkeypatch.setattr(
        client_module,
        "pool",
        SimpleNamespace(submit=lambda *_args, **_kwargs: None),
    )

    client._listen_ws()

    assert len(errors) == 1
    assert "another Web connection" in str(errors[0])
    assert websocket.close_count == 1
    assert client._active_ws is None


def test_stop_during_reconnect_backoff_cancels_without_another_attempt():
    client = bare_client()
    client.run_forever = True
    first_attempt = threading.Event()
    attempts = 0

    def listen_once(_thread=False):
        nonlocal attempts
        attempts += 1
        first_attempt.set()
        return True

    client._listen_ws_once = listen_once
    listener_thread = threading.Thread(
        target=client._listen_ws,
        kwargs={"reconnect": 60},
    )
    listener_thread.start()

    assert first_attempt.wait(1)
    client.stopListening()
    listener_thread.join(timeout=1)

    assert not listener_thread.is_alive()
    assert attempts == 1


def test_reconnect_loop_does_not_grow_the_python_call_stack():
    client = bare_client()
    client.run_forever = True
    attempts = 0

    def listen_once(_thread=False):
        nonlocal attempts
        attempts += 1
        return attempts < 1_200

    client._listen_ws_once = listen_once
    client._listen_ws(reconnect=0)

    assert attempts == 1_200


def test_transient_websocket_failure_reconnects_when_run_forever_is_false():
    client = bare_client()
    attempts = 0

    def listen_once(_thread=False):
        nonlocal attempts
        attempts += 1
        return attempts == 1

    client._listen_ws_once = listen_once
    client._listen_ws(reconnect=0)

    assert attempts == 2


def test_explicit_start_can_reset_stop_but_supervised_start_can_preserve_it():
    client = bare_client()
    client._state = SimpleNamespace(_config={"zpw_ws": ["wss://example.test/ws"]})
    stop_states = []
    client._listen_ws = lambda *_args, **_kwargs: stop_states.append(
        client._listen_stop.is_set()
    )

    client.stopListening()
    client.startListening(reset_stop=True)
    client.stopListening()
    client.startListening(reset_stop=False)

    assert stop_states == [False, True]
