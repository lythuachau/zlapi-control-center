from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from .video import VideoProcessingError, _run_media_command, resolve_media_executable


class GifProcessingError(ValueError):
    pass


@dataclass(frozen=True)
class GifMetadata:
    width: int
    height: int
    duration_ms: int
    frame_count: int
    size_bytes: int


@dataclass(frozen=True)
class PreparedGif:
    path: Path
    metadata: GifMetadata
    optimized: bool
    original_size_bytes: int


def probe_gif(
    path: Path,
    *,
    ffprobe_path: str,
    timeout_seconds: int,
) -> GifMetadata:
    try:
        executable = resolve_media_executable(ffprobe_path, "ffprobe")
        completed = _run_media_command(
            [
                executable,
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,nb_read_frames,duration:format=duration",
                "-of",
                "json",
                str(path),
            ],
            timeout_seconds=timeout_seconds,
            label="ffprobe GIF",
        )
    except VideoProcessingError as exc:
        raise GifProcessingError(str(exc)) from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip()[-500:] or "không đọc được tệp"
        raise GifProcessingError(f"GIF không hợp lệ: {detail}")
    try:
        payload = json.loads(completed.stdout)
        stream = (payload.get("streams") or [])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        duration = float(
            stream.get("duration")
            or (payload.get("format") or {}).get("duration")
            or 0
        )
        frame_count = int(stream.get("nb_read_frames") or 0)
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GifProcessingError("ffprobe trả về metadata GIF không hợp lệ") from exc
    if width <= 0 or height <= 0:
        raise GifProcessingError("GIF thiếu kích thước hợp lệ")
    return GifMetadata(
        width=width,
        height=height,
        duration_ms=max(1, round(duration * 1000)),
        frame_count=max(1, frame_count),
        size_bytes=path.stat().st_size,
    )


def prepare_gif_for_zalo(
    source_path: Path,
    output_dir: Path,
    *,
    max_bytes: int,
    max_width: int,
    max_height: int,
    supplied_width: int,
    supplied_height: int,
    ffmpeg_path: str,
    ffprobe_path: str,
    timeout_seconds: int,
) -> PreparedGif:
    """Return the original GIF when compatible, otherwise a bounded temporary copy."""
    original_size = source_path.stat().st_size
    if (
        original_size <= max_bytes
        and 0 < supplied_width <= max_width
        and 0 < supplied_height <= max_height
    ):
        return PreparedGif(
            path=source_path,
            metadata=GifMetadata(
                width=supplied_width,
                height=supplied_height,
                duration_ms=0,
                frame_count=0,
                size_bytes=original_size,
            ),
            optimized=False,
            original_size_bytes=original_size,
        )

    try:
        executable = resolve_media_executable(ffmpeg_path, "ffmpeg")
    except VideoProcessingError as exc:
        raise GifProcessingError(str(exc)) from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    target_bytes = max(256 * 1024, max_bytes - 64 * 1024)
    profiles = (
        (15, 128, max_width, max_height),
        (12, 96, min(max_width, 560), min(max_height, 420)),
        (10, 64, min(max_width, 480), min(max_height, 360)),
        (8, 48, min(max_width, 400), min(max_height, 300)),
    )
    last_detail = "không tạo được bản GIF phù hợp"
    for fps, colors, width, height in profiles:
        output_path = output_dir / f"zalo-gif-{secrets.token_hex(12)}.gif"
        filter_graph = (
            f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease:"
            "flags=lanczos,split[s0][s1];"
            f"[s0]palettegen=max_colors={colors}:stats_mode=diff[p];"
            "[s1][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
        )
        completed = _run_media_command(
            [
                executable,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                str(source_path),
                "-filter_complex",
                filter_graph,
                "-loop",
                "0",
                "-an",
                "-y",
                str(output_path),
            ],
            timeout_seconds=timeout_seconds,
            label="tối ưu GIF",
        )
        if completed.returncode != 0 or not output_path.is_file():
            output_path.unlink(missing_ok=True)
            last_detail = completed.stderr.strip()[-500:] or last_detail
            continue
        try:
            metadata = probe_gif(
                output_path,
                ffprobe_path=ffprobe_path,
                timeout_seconds=timeout_seconds,
            )
        except GifProcessingError as exc:
            output_path.unlink(missing_ok=True)
            last_detail = str(exc)
            continue
        if (
            metadata.size_bytes <= target_bytes
            and metadata.width <= max_width
            and metadata.height <= max_height
        ):
            return PreparedGif(
                path=output_path,
                metadata=metadata,
                optimized=True,
                original_size_bytes=original_size,
            )
        output_path.unlink(missing_ok=True)
        last_detail = (
            f"bản tối ưu vẫn có {metadata.size_bytes / 1024 / 1024:.2f} MB"
        )
    raise GifProcessingError(
        "GIF vượt giới hạn phiên Zalo và không thể tối ưu tự động: " + last_detail
    )
