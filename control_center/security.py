from __future__ import annotations

import base64
import ctypes
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings

ROLE_SCOPES: dict[str, set[str]] = {
    "viewer": {"read"},
    "operator": {"read", "accounts:use", "jobs:create", "jobs:manage", "uploads:create"},
    "admin": {
        "read",
        "accounts:read",
        "accounts:write",
        "accounts:use",
        "jobs:create",
        "jobs:manage",
        "uploads:create",
        "keys:manage",
        "users:read",
        "users:manage",
    },
    "super_admin": {"*"},
}

ROLE_LABELS = {
    "super_admin": "Super Admin",
    "admin": "Admin",
    "operator": "Operator",
    "viewer": "Viewer",
}

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def hash_secret(value: str) -> str:
    return password_hasher.hash(value)


def verify_secret(encoded: str, value: str) -> bool:
    try:
        return password_hasher.verify(encoded, value)
    except (VerifyMismatchError, InvalidHashError):
        return False


def generate_api_key() -> tuple[str, str, str]:
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    complete = f"zlc_{prefix}_{secret}"
    return prefix, complete, hash_secret(complete)


def parse_api_key(value: str) -> tuple[str, str] | None:
    parts = value.split("_", 2)
    if len(parts) != 3 or parts[0] != "zlc":
        return None
    return parts[1], value


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
    kind: str
    id: str
    name: str
    scopes: frozenset[str]
    role: str | None = None

    def can(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _dpapi_protect(data: bytes) -> bytes:
    source_buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(
        len(data),
        ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    cryptprotect_local_machine = 0x4
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "ZLAPI Control Center",
        None,
        None,
        None,
        cryptprotect_local_machine,
        ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    source_buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(
        len(data),
        ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


class SecretBox:
    """AES-GCM application secret encryption with a DPAPI-protected data key."""

    def __init__(self, settings: Settings):
        settings.ensure_directories()
        self.key_file = settings.secret_dir / "master-key.dpapi"
        self._key = self._load_or_create_key(settings)
        if len(self._key) != 32:
            raise ValueError("Master key must be exactly 32 bytes")
        self._cipher = AESGCM(self._key)

    def _load_or_create_key(self, settings: Settings) -> bytes:
        supplied = os.getenv("ZLAPI_MASTER_KEY", "")
        if supplied:
            return base64.urlsafe_b64decode(supplied.encode())

        if self.key_file.exists():
            payload = self.key_file.read_bytes()
            if os.name == "nt":
                return _dpapi_unprotect(payload)
            return base64.urlsafe_b64decode(payload)

        key = AESGCM.generate_key(bit_length=256)
        if os.name == "nt":
            payload = _dpapi_protect(key)
        elif settings.app_env == "production":
            raise ValueError("Set ZLAPI_MASTER_KEY when production is not running on Windows")
        else:
            payload = base64.urlsafe_b64encode(key)
        self.key_file.write_bytes(payload)
        if os.name != "nt":
            self.key_file.chmod(0o600)
        return key

    def encrypt_text(self, value: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, value.encode("utf-8"), b"zlapi-control-v1")
        return "v1:" + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt_text(self, value: str) -> str:
        version, encoded = value.split(":", 1)
        if version != "v1":
            raise ValueError("Unsupported encrypted value version")
        raw = base64.urlsafe_b64decode(encoded)
        return self._cipher.decrypt(raw[:12], raw[12:], b"zlapi-control-v1").decode("utf-8")

    def encrypt_json(self, value: dict) -> str:
        return self.encrypt_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    def decrypt_json(self, value: str) -> dict:
        decoded = json.loads(self.decrypt_text(value))
        if not isinstance(decoded, dict):
            raise ValueError("Encrypted JSON secret must contain an object")
        return decoded

    def encrypt_bytes(self, value: bytes) -> bytes:
        nonce = os.urandom(12)
        return b"ZLC1" + nonce + self._cipher.encrypt(nonce, value, b"zlapi-archive-v1")

    def decrypt_bytes(self, value: bytes) -> bytes:
        if not value.startswith(b"ZLC1"):
            raise ValueError("Unsupported archive format")
        nonce = value[4:16]
        return self._cipher.decrypt(nonce, value[16:], b"zlapi-archive-v1")


def is_expired(value: datetime | None) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value <= datetime.now(UTC)


def safe_child_path(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
