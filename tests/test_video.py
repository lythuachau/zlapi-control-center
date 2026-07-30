from __future__ import annotations

import json
import subprocess

import pytest

from control_center.video import (
    VideoMetadata,
    VideoProcessingError,
    generate_video_thumbnail,
    probe_video,
)


def test_probe_video_uses_server_metadata(tmp_path, monkeypatch):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fixture")

    def fake_run(arguments, **_kwargs):
        assert "-show_entries" in arguments
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "h264",
                            "width": 836,
                            "height": 480,
                            "duration": "12.041667",
                        }
                    ],
                    "format": {
                        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                        "duration": "12.072925",
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("control_center.video.resolve_media_executable", lambda *_args: "ffprobe")
    monkeypatch.setattr("control_center.video.subprocess.run", fake_run)

    metadata = probe_video(
        video_path,
        ffprobe_path="ffprobe",
        timeout_seconds=30,
    )

    assert metadata == VideoMetadata(
        width=836,
        height=480,
        duration_ms=12042,
        codec_name="h264",
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
    )


def test_probe_video_rejects_incompatible_codec(tmp_path, monkeypatch):
    video_path = tmp_path / "clip.webm"
    video_path.write_bytes(b"fixture")

    monkeypatch.setattr("control_center.video.resolve_media_executable", lambda *_args: "ffprobe")
    monkeypatch.setattr(
        "control_center.video.subprocess.run",
        lambda arguments, **_kwargs: subprocess.CompletedProcess(
            arguments,
            0,
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "vp9",
                            "width": 1280,
                            "height": 720,
                            "duration": "5.0",
                        }
                    ],
                    "format": {"format_name": "matroska,webm", "duration": "5.0"},
                }
            ),
            stderr="",
        ),
    )

    with pytest.raises(VideoProcessingError, match="MP4/MOV"):
        probe_video(
            video_path,
            ffprobe_path="ffprobe",
            timeout_seconds=30,
        )


def test_generate_video_thumbnail_requires_nonempty_output(tmp_path, monkeypatch):
    video_path = tmp_path / "clip.mp4"
    thumbnail_path = tmp_path / "thumb.jpg"
    video_path.write_bytes(b"fixture")
    metadata = VideoMetadata(836, 480, 12073, "h264", "mp4")

    monkeypatch.setattr("control_center.video.resolve_media_executable", lambda *_args: "ffmpeg")
    monkeypatch.setattr(
        "control_center.video.subprocess.run",
        lambda arguments, **_kwargs: subprocess.CompletedProcess(
            arguments,
            0,
            stdout="",
            stderr="",
        ),
    )

    with pytest.raises(VideoProcessingError, match="thumbnail"):
        generate_video_thumbnail(
            video_path,
            thumbnail_path,
            metadata,
            ffmpeg_path="ffmpeg",
            timeout_seconds=30,
        )
