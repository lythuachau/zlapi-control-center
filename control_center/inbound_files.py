from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import requests

ALLOWED_ZALO_FILE_HOST_SUFFIXES = (
    "dlfl.vn",
    "flchat.vn",
    "zadn.vn",
    "zdn.vn",
    "zalo.me",
    "zaloapp.com",
)
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
RANGE_PATTERN = re.compile(r"^bytes=(?:\d+-\d*|-\d+)$")
MAX_REDIRECTS = 4


class InboundFileError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class OpenedInboundFile:
    response: requests.Response
    status_code: int
    headers: dict[str, str]
    max_bytes: int

    def chunks(self) -> Iterator[bytes]:
        total = 0
        try:
            for chunk in self.response.iter_content(chunk_size=128 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > self.max_bytes:
                    raise InboundFileError(413, "Tệp vượt quá giới hạn tải xuống")
                yield chunk
        finally:
            self.response.close()


def is_allowed_zalo_file_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").rstrip(".").lower()
    return (
        parsed.scheme.lower() == "https"
        and port in {None, 443}
        and bool(host)
        and any(host == suffix or host.endswith(f".{suffix}") for suffix in ALLOWED_ZALO_FILE_HOST_SUFFIXES)
        and not parsed.username
        and not parsed.password
    )


def safe_inbound_filename(value: str) -> str:
    name = Path(str(value or "tep-dinh-kem")).name[:255] or "tep-dinh-kem"
    cleaned = "".join("_" if ord(char) < 32 or char in {'"', "\\"} else char for char in name)
    return cleaned.strip(" .") or "tep-dinh-kem"


def attachment_disposition(filename: str) -> str:
    safe_name = safe_inbound_filename(filename)
    ascii_name = unicodedata.normalize("NFKD", safe_name).encode("ascii", "ignore").decode()
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", ascii_name).strip(" .")
    if not ascii_name:
        ascii_name = f"download{Path(safe_name).suffix[:16]}"
    return f"attachment; filename=\"{ascii_name[:180]}\"; filename*=UTF-8''{quote(safe_name, safe='')}"


def _content_length(response: requests.Response) -> int | None:
    raw = response.headers.get("content-length")
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def open_inbound_file(
    url: str,
    *,
    filename: str,
    range_header: str | None,
    max_bytes: int,
    request_get: Callable[..., requests.Response] = requests.get,
) -> OpenedInboundFile:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    forwarded_range = (range_header or "").strip()
    if forwarded_range and not RANGE_PATTERN.fullmatch(forwarded_range):
        raise InboundFileError(416, "Range tải xuống không hợp lệ")

    current_url = str(url or "")
    response: requests.Response | None = None
    for redirect_count in range(MAX_REDIRECTS + 1):
        if not is_allowed_zalo_file_url(current_url):
            raise InboundFileError(502, "Liên kết tệp không thuộc CDN Zalo được phép")
        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "User-Agent": "Mozilla/5.0 (compatible; ZLAPI-Control-Center/1.0)",
        }
        if forwarded_range:
            headers["Range"] = forwarded_range
        try:
            response = request_get(
                current_url,
                headers=headers,
                allow_redirects=False,
                stream=True,
                timeout=(10, 60),
            )
        except requests.Timeout as exc:
            raise InboundFileError(504, "CDN Zalo phản hồi quá thời gian") from exc
        except requests.RequestException as exc:
            raise InboundFileError(502, "Không thể kết nối CDN Zalo") from exc

        if response.status_code in REDIRECT_STATUSES:
            location = response.headers.get("location")
            response.close()
            response = None
            if not location or redirect_count >= MAX_REDIRECTS:
                raise InboundFileError(502, "CDN Zalo chuyển hướng không hợp lệ")
            current_url = urljoin(current_url, location)
            continue
        break

    if response is None:
        raise InboundFileError(502, "Không thể mở tệp từ CDN Zalo")
    if response.status_code in {404, 410}:
        response.close()
        raise InboundFileError(410, "Tệp không còn tồn tại trên CDN Zalo")
    if response.status_code == 416:
        response.close()
        raise InboundFileError(416, "CDN Zalo từ chối Range tải xuống")
    if response.status_code not in {200, 206}:
        response.close()
        raise InboundFileError(502, "CDN Zalo từ chối tải tệp")

    content_length = _content_length(response)
    if content_length is not None and content_length > max_bytes:
        response.close()
        raise InboundFileError(413, "Tệp vượt quá giới hạn tải xuống")

    output_headers = {
        "Content-Disposition": attachment_disposition(filename),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    for source, target in (
        ("content-length", "Content-Length"),
        ("content-range", "Content-Range"),
        ("accept-ranges", "Accept-Ranges"),
    ):
        value = response.headers.get(source)
        if value:
            output_headers[target] = value
    content_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    if not content_type or content_type.lower() == "text/html":
        content_type = "application/octet-stream"
    output_headers["Content-Type"] = content_type
    return OpenedInboundFile(
        response=response,
        status_code=response.status_code,
        headers=output_headers,
        max_bytes=max_bytes,
    )
