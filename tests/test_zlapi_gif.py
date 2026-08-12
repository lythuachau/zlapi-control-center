from __future__ import annotations

from types import SimpleNamespace

from zlapi._client import ZaloAPI


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def bare_client(config: dict) -> ZaloAPI:
    client = object.__new__(ZaloAPI)
    client._state = SimpleNamespace(_config=config)
    return client


def test_gif_limits_come_from_current_login_settings():
    client = bare_client(
        {
            "settings": {
                "gif": {
                    "max_gif_size": 3072,
                    "max_width": 640,
                    "max_height": 480,
                },
                "sharefile": {"max_size_gif": 10},
            }
        }
    )

    assert client.getGifUploadLimits() == {
        "max_bytes": 3 * 1024 * 1024,
        "max_width": 640,
        "max_height": 480,
    }


def test_gif_thumbnail_uses_dynamic_file_service_and_decodes_url():
    client = bare_client(
        {"zpw_service_map_v3": {"file": ["https://files.example.test"]}}
    )
    calls = []
    client._post = lambda url, **kwargs: (
        calls.append((url, kwargs))
        or FakeResponse({"error_code": 0, "data": "encrypted"})
    )
    client._decode = lambda _payload: {
        "data": {"url": "https://cdn.example.test/thumb.gif"}
    }

    assert client._uploadGifThumbnail(b"gif-data") == "https://cdn.example.test/thumb.gif"
    assert calls[0][0] == "https://files.example.test/api/message/upthumb"
    assert calls[0][1]["files"]["fileContent"][1] == b"gif-data"
