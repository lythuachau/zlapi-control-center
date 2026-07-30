from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import quote

from .config import Settings

DANGEROUS_UPLOAD_EXTENSIONS = {
    ".app",
    ".bat",
    ".cmd",
    ".com",
    ".cpl",
    ".dll",
    ".dmg",
    ".exe",
    ".hta",
    ".htm",
    ".html",
    ".iso",
    ".jar",
    ".js",
    ".jse",
    ".lnk",
    ".msi",
    ".msp",
    ".pif",
    ".ps1",
    ".psd1",
    ".psm1",
    ".reg",
    ".scr",
    ".sh",
    ".svg",
    ".vbe",
    ".vbs",
    ".wsf",
    ".wsh",
}

IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".3gp", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".webm"}


def safe_upload_name(value: str) -> str:
    return Path(value or "upload").name[:255] or "upload"


def validate_upload_name(value: str) -> str:
    name = safe_upload_name(value)
    suffix = Path(name).suffix.lower()
    if suffix in DANGEROUS_UPLOAD_EXTENSIONS:
        raise ValueError(f"Không cho phép tải lên tệp {suffix or 'này'}")
    return name


def infer_media_kind(filename: str, content_type: str | None) -> str:
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    suffix = Path(filename).suffix.lower()
    if mime.startswith("image/") or suffix in IMAGE_EXTENSIONS:
        return "image"
    if mime.startswith("video/") or suffix in VIDEO_EXTENSIONS:
        return "video"
    if mime.startswith("audio/") or suffix in AUDIO_EXTENSIONS:
        return "audio"
    return "file"


def validate_media_kind(filename: str, content_type: str | None, declared_kind: str) -> str:
    name = validate_upload_name(filename)
    detected_kind = infer_media_kind(name, content_type)
    if declared_kind != detected_kind:
        raise ValueError(
            f"Loại tệp {name} không khớp: hệ thống nhận diện là {detected_kind}"
        )
    return detected_kind


def outbound_signature(settings: Settings, upload_id: str, expires: int) -> str:
    payload = f"{upload_id}:{expires}".encode()
    return hmac.new(
        settings.session_secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()


def verify_outbound_signature(
    settings: Settings,
    upload_id: str,
    expires: int,
    signature: str,
) -> bool:
    if expires < int(time.time()):
        return False
    expected = outbound_signature(settings, upload_id, expires)
    return hmac.compare_digest(expected, signature)


def outbound_media_url(
    settings: Settings,
    upload_id: str,
    *,
    expires: int | None = None,
) -> str:
    expires = expires or int(time.time()) + (24 * 60 * 60)
    signature = outbound_signature(settings, upload_id, expires)
    scheme = "https" if settings.app_env == "production" else "http"
    domain = settings.domain.strip().rstrip("/")
    if domain.startswith(("http://", "https://")):
        base = domain
    else:
        base = f"{scheme}://{domain}"
    return (
        f"{base}/media/outbound/{quote(upload_id, safe='')}"
        f"?expires={expires}&signature={signature}"
    )
