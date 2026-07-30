from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from control_center.connector import ZaloConnector, ZaloResponseError
from control_center.db import Database
from control_center.models import MediaUpload, ZaloAccount, utcnow
from control_center.video import VideoMetadata


class FakeMediaClient:
    def __init__(self):
        self.calls = []

    def sendLocalImage(self, path, thread_id, thread_type, **kwargs):
        self.calls.append(("image", path, thread_id, thread_type, kwargs))
        return {"sent": "image"}

    def sendRemoteFile(self, url, thread_id, thread_type, **kwargs):
        self.calls.append(("file", url, thread_id, thread_type, kwargs))
        return {"sent": "file"}

    def sendMessage(self, message, thread_id, thread_type):
        self.calls.append(("message", message.text, thread_id, thread_type))
        return {"sent": "message"}

    def sendRemoteVideo(
        self,
        video_url,
        thumbnail_url,
        duration,
        thread_id,
        thread_type,
        **kwargs,
    ):
        self.calls.append(
            (
                "video",
                video_url,
                thumbnail_url,
                duration,
                thread_id,
                thread_type,
                kwargs,
            )
        )
        return {"data": {"msgId": "video-message-1"}}


def test_connector_sends_mixed_media_without_automatic_retry(settings, tmp_path):
    settings.domain = "zlapi.example.test"
    settings.ensure_directories()
    database = Database(settings)
    database.create_schema()
    image_path = settings.upload_dir / "image.png"
    file_path = settings.upload_dir / "guide.pdf"
    image_path.write_bytes(b"image")
    file_path.write_bytes(b"document")
    client = FakeMediaClient()
    connector = ZaloConnector(settings, object())
    connector.client_for = lambda _account: client

    with database.session_factory() as session:
        account = ZaloAccount(
            name="Media connector",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        session.add(account)
        session.flush()
        session.add_all(
            [
                MediaUpload(
                    id="a" * 32,
                    original_name="image.png",
                    stored_path=str(image_path),
                    size_bytes=image_path.stat().st_size,
                    content_type="image/png",
                    expires_at=utcnow() + timedelta(days=1),
                ),
                MediaUpload(
                    id="b" * 32,
                    original_name="guide.pdf",
                    stored_path=str(file_path),
                    size_bytes=file_path.stat().st_size,
                    content_type="application/pdf",
                    expires_at=utcnow() + timedelta(days=1),
                ),
            ]
        )
        session.commit()
        result = connector.send_chat_media(
            session,
            account,
            {
                "thread_id": "destination",
                "thread_type": "user",
                "text": "Tài liệu tham khảo",
                "items": [
                    {
                        "upload_token": f"upload:{'a' * 32}",
                        "kind": "image",
                        "width": 800,
                        "height": 600,
                    },
                    {
                        "upload_token": f"upload:{'b' * 32}",
                        "kind": "file",
                    },
                ],
            },
        )

    assert result["count"] == 2
    assert [call[0] for call in client.calls] == ["image", "file", "message"]
    assert client.calls[1][1].startswith(
        "http://zlapi.example.test/media/outbound/"
    )
    assert client.calls[2][1] == "Tài liệu tham khảo"
    database.dispose()


def _prepare_video_test(
    settings,
    monkeypatch,
    *,
    response,
):
    settings.domain = "zlapi.example.test"
    settings.ensure_directories()
    database = Database(settings)
    database.create_schema()
    video_path = settings.upload_dir / "clip.mp4"
    video_path.write_bytes(b"valid-video-fixture")
    client = FakeMediaClient()

    def send_remote_video(*args, **kwargs):
        FakeMediaClient.sendRemoteVideo(client, *args, **kwargs)
        return response

    client.sendRemoteVideo = send_remote_video
    connector = ZaloConnector(settings, object())
    connector.client_for = lambda _account: client
    monkeypatch.setattr(
        "control_center.connector.probe_video",
        lambda *_args, **_kwargs: VideoMetadata(
            width=836,
            height=480,
            duration_ms=12073,
            codec_name="h264",
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
        ),
    )

    def fake_thumbnail(_video_path, thumbnail_path, _metadata, **_kwargs):
        thumbnail_path.write_bytes(b"jpeg-thumbnail")

    monkeypatch.setattr(
        "control_center.connector.generate_video_thumbnail",
        fake_thumbnail,
    )
    return database, video_path, client, connector


def test_connector_probes_video_and_sends_signed_thumbnail(settings, monkeypatch):
    database, video_path, client, connector = _prepare_video_test(
        settings,
        monkeypatch,
        response={"data": {"msgId": "video-message-1"}},
    )

    with database.session_factory() as session:
        account = ZaloAccount(
            name="Video connector",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        upload = MediaUpload(
            id="c" * 32,
            original_name="clip.mp4",
            stored_path=str(video_path),
            size_bytes=video_path.stat().st_size,
            content_type="video/mp4",
            expires_at=utcnow() + timedelta(days=1),
        )
        session.add_all([account, upload])
        session.commit()

        result = connector.send_chat_media(
            session,
            account,
            {
                "thread_id": "video-destination",
                "thread_type": "user",
                "text": "",
                "items": [
                    {
                        "upload_token": f"upload:{'c' * 32}",
                        "kind": "video",
                        # Stale browser values must not be trusted.
                        "width": None,
                        "height": None,
                        "duration_ms": None,
                    }
                ],
            },
        )

        uploads = session.scalar(select(func.count(MediaUpload.id)))
        thumbnail = session.scalar(
            select(MediaUpload).where(MediaUpload.content_type == "image/jpeg")
        )

    assert result["items"][0]["metadata"] == {
        "width": 836,
        "height": 480,
        "duration_ms": 12073,
        "codec": "h264",
    }
    assert uploads == 2
    assert thumbnail is not None
    assert Path(thumbnail.stored_path).read_bytes() == b"jpeg-thumbnail"
    video_call = client.calls[0]
    assert video_call[0] == "video"
    assert video_call[1].startswith(
        f"http://zlapi.example.test/media/outbound/{'c' * 32}"
    )
    assert video_call[2].startswith(
        f"http://zlapi.example.test/media/outbound/{thumbnail.id}"
    )
    assert video_call[3] == 12073
    assert video_call[6]["width"] == 836
    assert video_call[6]["height"] == 480
    database.dispose()


def test_connector_marks_embedded_zalo_video_error_as_failure(settings, monkeypatch):
    database, video_path, _client, connector = _prepare_video_test(
        settings,
        monkeypatch,
        response={
            "error_code": 114,
            "error_message": "Tham số không hợp lệ",
            "data": None,
        },
    )

    with database.session_factory() as session:
        account = ZaloAccount(
            name="Video connector",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        session.add(account)
        session.add(
            MediaUpload(
                id="d" * 32,
                original_name="clip.mp4",
                stored_path=str(video_path),
                size_bytes=video_path.stat().st_size,
                content_type="video/mp4",
                expires_at=utcnow() + timedelta(days=1),
            )
        )
        session.commit()

        with pytest.raises(ZaloResponseError, match=r"Zalo #114"):
            connector.send_chat_media(
                session,
                account,
                {
                    "thread_id": "video-destination",
                    "thread_type": "user",
                    "items": [
                        {
                            "upload_token": f"upload:{'d' * 32}",
                            "kind": "video",
                        }
                    ],
                },
            )

    database.dispose()
