from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class VideoProcessingError(ValueError):
    pass


@dataclass(frozen=True)
class VideoMetadata:
    width: int
    height: int
    duration_ms: int
    codec_name: str
    format_name: str


def resolve_media_executable(value: str, label: str) -> str:
    configured = str(value or "").strip()
    if not configured:
        raise VideoProcessingError(f"Máy chủ chưa cấu hình {label}")

    candidate = Path(configured)
    if candidate.is_absolute() and candidate.is_file():
        return str(candidate)

    resolved = shutil.which(configured)
    if resolved:
        return resolved
    raise VideoProcessingError(
        f"Máy chủ không tìm thấy {label}. Quản trị viên cần kiểm tra cấu hình media."
    )


def _run_media_command(
    arguments: list[str],
    *,
    timeout_seconds: int,
    label: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoProcessingError(f"{label} quá thời gian xử lý cho phép") from exc
    except OSError as exc:
        raise VideoProcessingError(f"Không thể chạy {label}: {exc}") from exc


def probe_video(
    path: Path,
    *,
    ffprobe_path: str,
    timeout_seconds: int,
) -> VideoMetadata:
    executable = resolve_media_executable(ffprobe_path, "ffprobe")
    completed = _run_media_command(
        [
            executable,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,duration:format=format_name,duration",
            "-of",
            "json",
            str(path),
        ],
        timeout_seconds=timeout_seconds,
        label="ffprobe",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-500:] or "không đọc được tệp"
        raise VideoProcessingError(f"Video không hợp lệ: {detail}")

    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise VideoProcessingError("ffprobe trả về metadata không hợp lệ") from exc

    streams = payload.get("streams")
    streams = streams if isinstance(streams, list) else []
    video_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        raise VideoProcessingError("Tệp không có luồng video")

    try:
        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
        duration_seconds = float(
            video_stream.get("duration")
            or (payload.get("format") or {}).get("duration")
            or 0
        )
    except (TypeError, ValueError) as exc:
        raise VideoProcessingError("Metadata kích thước hoặc thời lượng video không hợp lệ") from exc

    if width <= 0 or height <= 0 or duration_seconds <= 0:
        raise VideoProcessingError("Video thiếu kích thước hoặc thời lượng hợp lệ")

    codec_name = str(video_stream.get("codec_name") or "").lower()
    format_name = str((payload.get("format") or {}).get("format_name") or "").lower()
    if codec_name != "h264" or not any(
        container in format_name for container in ("mp4", "mov", "3gp")
    ):
        raise VideoProcessingError(
            "Video chưa tương thích với Zalo; hãy dùng MP4/MOV mã hóa H.264"
        )

    return VideoMetadata(
        width=width,
        height=height,
        duration_ms=max(1, round(duration_seconds * 1000)),
        codec_name=codec_name,
        format_name=format_name,
    )


def generate_video_thumbnail(
    video_path: Path,
    thumbnail_path: Path,
    metadata: VideoMetadata,
    *,
    ffmpeg_path: str,
    timeout_seconds: int,
) -> None:
    executable = resolve_media_executable(ffmpeg_path, "ffmpeg")
    seek_seconds = min(2.0, max(0.1, metadata.duration_ms / 10_000))
    completed = _run_media_command(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{seek_seconds:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-an",
            "-vf",
            r"scale=min(1280\,iw):-2",
            "-q:v",
            "3",
            "-y",
            str(thumbnail_path),
        ],
        timeout_seconds=timeout_seconds,
        label="ffmpeg",
    )
    if completed.returncode != 0:
        thumbnail_path.unlink(missing_ok=True)
        detail = completed.stderr.strip()[-500:] or "không tạo được ảnh đại diện"
        raise VideoProcessingError(f"Không thể tạo thumbnail video: {detail}")
    if not thumbnail_path.is_file() or thumbnail_path.stat().st_size <= 0:
        thumbnail_path.unlink(missing_ok=True)
        raise VideoProcessingError("ffmpeg không tạo được thumbnail video")
