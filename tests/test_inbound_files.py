from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from control_center.inbound_files import (
    InboundFileError,
    attachment_disposition,
    is_allowed_zalo_file_url,
    open_inbound_file,
)


@dataclass
class FakeResponse:
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body_chunks: list[bytes] = field(default_factory=list)
    closed: bool = False

    def iter_content(self, chunk_size: int):
        assert chunk_size > 0
        yield from self.body_chunks

    def close(self):
        self.closed = True


def test_zalo_file_url_allowlist_rejects_untrusted_and_credentialed_urls():
    assert is_allowed_zalo_file_url("https://f41-zfcloud.zdn.vn/file")
    assert is_allowed_zalo_file_url("https://file-stal-4.flchat.vn/file")
    assert not is_allowed_zalo_file_url("http://f41-zfcloud.zdn.vn/file")
    assert not is_allowed_zalo_file_url("https://zdn.vn.evil.example/file")
    assert not is_allowed_zalo_file_url("https://user:pass@f41-zfcloud.zdn.vn/file")


def test_attachment_disposition_preserves_utf8_and_removes_header_controls():
    value = attachment_disposition('báo-cáo\r\n"tháng-8".txt')
    assert value.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in value
    assert "\r" not in value
    assert "\n" not in value
    assert '%22' not in value


def test_open_inbound_file_follows_only_allowed_redirects_and_forwards_range():
    redirect = FakeResponse(
        301,
        {"location": "https://file-stal-4.flchat.vn/download"},
    )
    content = FakeResponse(
        206,
        {
            "content-type": "application/octet-stream",
            "content-length": "4",
            "content-range": "bytes 0-3/10",
            "accept-ranges": "bytes",
        },
        [b"data"],
    )
    responses = iter((redirect, content))
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return next(responses)

    opened = open_inbound_file(
        "https://file-stal-4.dlfl.vn/download",
        filename="tai-lieu.rar",
        range_header="bytes=0-3",
        max_bytes=1024,
        request_get=fake_get,
    )
    assert b"".join(opened.chunks()) == b"data"
    assert opened.status_code == 206
    assert opened.headers["Content-Range"] == "bytes 0-3/10"
    assert opened.headers["Content-Disposition"].startswith("attachment;")
    assert calls[0][1]["allow_redirects"] is False
    assert calls[1][1]["headers"]["Range"] == "bytes=0-3"
    assert redirect.closed is True
    assert content.closed is True


def test_open_inbound_file_rejects_redirect_to_untrusted_host():
    redirect = FakeResponse(302, {"location": "https://evil.example/file"})

    with pytest.raises(InboundFileError) as raised:
        open_inbound_file(
            "https://file-stal-4.dlfl.vn/download",
            filename="tai-lieu.txt",
            range_header=None,
            max_bytes=1024,
            request_get=lambda *_args, **_kwargs: redirect,
        )
    assert raised.value.status_code == 502
    assert redirect.closed is True


def test_open_inbound_file_enforces_declared_and_streamed_size_limits():
    declared = FakeResponse(200, {"content-length": "2048"})
    with pytest.raises(InboundFileError) as declared_error:
        open_inbound_file(
            "https://f41-zfcloud.zdn.vn/file",
            filename="large.rar",
            range_header=None,
            max_bytes=1024,
            request_get=lambda *_args, **_kwargs: declared,
        )
    assert declared_error.value.status_code == 413
    assert declared.closed is True

    streamed = FakeResponse(200, {}, [b"a" * 700, b"b" * 700])
    opened = open_inbound_file(
        "https://f41-zfcloud.zdn.vn/file",
        filename="large.rar",
        range_header=None,
        max_bytes=1024,
        request_get=lambda *_args, **_kwargs: streamed,
    )
    with pytest.raises(InboundFileError) as streamed_error:
        list(opened.chunks())
    assert streamed_error.value.status_code == 413
    assert streamed.closed is True


def test_open_inbound_file_rejects_malformed_range_without_network_call():
    called = False

    def fake_get(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network should not be called")

    with pytest.raises(InboundFileError) as raised:
        open_inbound_file(
            "https://f41-zfcloud.zdn.vn/file",
            filename="file.txt",
            range_header="bytes=-",
            max_bytes=1024,
            request_get=fake_get,
        )
    assert raised.value.status_code == 416
    assert called is False
