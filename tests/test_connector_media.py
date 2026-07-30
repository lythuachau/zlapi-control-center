from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from control_center.connector import ZaloConnector, ZaloResponseError
from control_center.db import Database
from control_center.models import GifLibraryItem, MediaUpload, ZaloAccount, utcnow
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

    def sendRemoteVoice(self, url, thread_id, thread_type, **kwargs):
        self.calls.append(("audio", url, thread_id, thread_type, kwargs))
        return {"sent": "audio"}

    def sendLocalGif(self, path, thumbnail_url, thread_id, thread_type, **kwargs):
        self.calls.append(
            ("gif", path, thumbnail_url, thread_id, thread_type, kwargs)
        )
        return {"sent": "gif"}

    def sendSticker(
        self,
        sticker_type,
        sticker_id,
        category_id,
        thread_id,
        thread_type,
    ):
        self.calls.append(
            (
                "sticker",
                sticker_type,
                sticker_id,
                category_id,
                thread_id,
                thread_type,
            )
        )
        return {"sent": "sticker"}

    def sendCustomSticker(
        self,
        static_url,
        animation_url,
        thread_id,
        thread_type,
        **kwargs,
    ):
        self.calls.append(
            (
                "custom-sticker",
                static_url,
                animation_url,
                thread_id,
                thread_type,
                kwargs,
            )
        )
        return {"sent": "custom-sticker"}

    def sendLink(self, url, title, thread_id, thread_type, **kwargs):
        self.calls.append(("link", url, title, thread_id, thread_type, kwargs))
        return {"sent": "link"}

    def sendBusinessCard(
        self,
        user_id,
        qr_url,
        thread_id,
        thread_type,
        **kwargs,
    ):
        self.calls.append(
            ("contact", user_id, qr_url, thread_id, thread_type, kwargs)
        )
        return {"sent": "contact"}

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


def test_connector_sends_rich_media_and_audio(settings):
    settings.domain = "zlapi.example.test"
    settings.ensure_directories()
    database = Database(settings)
    database.create_schema()
    gif_path = settings.upload_dir / "reaction.gif"
    library_gif_path = settings.gif_library_dir / "saved.gif"
    audio_path = settings.upload_dir / "voice.mp3"
    gif_path.write_bytes(b"gif-fixture")
    library_gif_path.write_bytes(b"saved-gif-fixture")
    audio_path.write_bytes(b"audio-fixture")
    client = FakeMediaClient()
    connector = ZaloConnector(settings, object())
    connector.client_for = lambda _account: client

    with database.session_factory() as session:
        account = ZaloAccount(
            name="Rich media connector",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        session.add(account)
        session.add_all(
            [
                MediaUpload(
                    id="e" * 32,
                    original_name="reaction.gif",
                    stored_path=str(gif_path),
                    size_bytes=gif_path.stat().st_size,
                    content_type="image/gif",
                    expires_at=utcnow() + timedelta(days=1),
                ),
                GifLibraryItem(
                    id="saved-gif-id",
                    sha256="1" * 64,
                    original_name="saved.gif",
                    stored_path=str(library_gif_path),
                    size_bytes=library_gif_path.stat().st_size,
                    content_type="image/gif",
                    width=480,
                    height=320,
                ),
                MediaUpload(
                    id="f" * 32,
                    original_name="voice.mp3",
                    stored_path=str(audio_path),
                    size_bytes=audio_path.stat().st_size,
                    content_type="audio/mpeg",
                    expires_at=utcnow() + timedelta(days=1),
                ),
            ]
        )
        session.commit()

        sticker = connector.send_chat_rich_media(
            session,
            account,
            {
                "thread_id": "destination",
                "thread_type": "user",
                "kind": "sticker",
                "media": {
                    "sticker_id": "43521",
                    "category_id": "11786",
                    "sticker_type": 3,
                },
            },
        )
        gif = connector.send_chat_rich_media(
            session,
            account,
            {
                "thread_id": "destination",
                "thread_type": "user",
                "kind": "gif",
                "media": {
                    "upload_token": f"upload:{'e' * 32}",
                    "thumbnail_url": "https://zlapi.example.test/reaction.gif",
                    "width": 320,
                    "height": 240,
                },
            },
        )
        library_gif = connector.send_chat_rich_media(
            session,
            account,
            {
                "thread_id": "destination",
                "thread_type": "user",
                "kind": "gif",
                "media": {
                    "library_gif_id": "saved-gif-id",
                    "width": 480,
                    "height": 320,
                },
            },
        )
        link = connector.send_chat_rich_media(
            session,
            account,
            {
                "thread_id": "destination",
                "thread_type": "user",
                "kind": "link",
                "text": "Đọc thêm",
                "media": {
                    "url": "https://example.com/guide",
                    "title": "Hướng dẫn",
                    "description": "Tài liệu tham khảo",
                },
            },
        )
        contact = connector.send_chat_rich_media(
            session,
            account,
            {
                "thread_id": "destination",
                "thread_type": "user",
                "kind": "contact",
                "media": {
                    "contact_user_id": "friend-123",
                    "contact_qr_url": "",
                    "contact_phone": "0900000001",
                },
            },
        )
        audio = connector.send_chat_media(
            session,
            account,
            {
                "thread_id": "destination",
                "thread_type": "user",
                "items": [
                    {
                        "upload_token": f"upload:{'f' * 32}",
                        "kind": "audio",
                    }
                ],
            },
        )

    assert [sticker["kind"], gif["kind"], library_gif["kind"], link["kind"], contact["kind"]] == [
        "sticker",
        "gif",
        "gif",
        "link",
        "contact",
    ]
    assert audio["count"] == 1
    assert [call[0] for call in client.calls] == [
        "sticker",
        "gif",
        "gif",
        "link",
        "contact",
        "audio",
    ]
    assert client.calls[0][1:4] == (3, 43521, 11786)
    assert client.calls[1][5]["width"] == 320
    assert client.calls[2][1] == str(library_gif_path)
    assert "/media/gif-library/saved-gif-id" in client.calls[2][2]
    assert client.calls[3][5]["message"].text == "Đọc thêm"
    assert client.calls[4][1] == "friend-123"
    assert client.calls[5][1].startswith(
        f"http://zlapi.example.test/media/outbound/{'f' * 32}"
    )
    database.dispose()


def test_connector_replies_to_source_message_with_automation_sticker(settings):
    database = Database(settings)
    database.create_schema()
    client = FakeMediaClient()
    connector = ZaloConnector(settings, object())
    connector.client_for = lambda _account: client

    with database.session_factory() as session:
        account = ZaloAccount(
            name="Automation connector",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        session.add(account)
        session.flush()
        result = connector.send_chat_automation_sticker(
            session,
            account,
            {
                "thread_id": "group-123",
                "thread_type": "group",
                "reply_message_id": "message-456",
                "media": {
                    "thumbnail_url": "https://example.com/sticker.webp",
                    "width": 140,
                    "height": 120,
                },
            },
        )

    assert result["automation"] is True
    assert result["reply_message_id"] == "message-456"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call[:4] == (
        "custom-sticker",
        "https://example.com/sticker.webp",
        "https://example.com/sticker.webp",
        "group-123",
    )
    assert call[4].name == "GROUP"
    assert call[5] == {"reply": "message-456", "width": 140, "height": 120}
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
