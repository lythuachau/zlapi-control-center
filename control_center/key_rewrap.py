from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ENVELOPE_VERSION = 1
ENVELOPE_ALGORITHM = "RSA-OAEP-SHA256"


@dataclass(frozen=True)
class RewrapEnvelope:
    certificate_sha256: str
    ciphertext: bytes

    def to_bytes(self) -> bytes:
        return (
            json.dumps(
                {
                    "version": ENVELOPE_VERSION,
                    "algorithm": ENVELOPE_ALGORITHM,
                    "certificate_sha256": self.certificate_sha256,
                    "ciphertext": base64.b64encode(self.ciphertext).decode("ascii"),
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            + b"\n"
        )

    @classmethod
    def from_bytes(cls, value: bytes) -> RewrapEnvelope:
        payload = json.loads(value.decode("ascii"))
        if payload.get("version") != ENVELOPE_VERSION:
            raise ValueError("Unsupported rewrap envelope version")
        if payload.get("algorithm") != ENVELOPE_ALGORITHM:
            raise ValueError("Unsupported rewrap envelope algorithm")
        fingerprint = str(payload.get("certificate_sha256") or "").lower()
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise ValueError("Invalid certificate fingerprint")
        ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
        if not ciphertext:
            raise ValueError("Empty rewrap ciphertext")
        return cls(fingerprint, ciphertext)


def certificate_fingerprint(certificate: x509.Certificate) -> str:
    der = certificate.public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


def wrap_data_key(data_key: bytes, certificate: x509.Certificate) -> RewrapEnvelope:
    if len(data_key) != 32:
        raise ValueError("ZLAPI data key must be exactly 32 bytes")
    public_key = certificate.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 3072:
        raise ValueError("Rewrap certificate must use RSA with at least 3072 bits")
    ciphertext = public_key.encrypt(
        data_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return RewrapEnvelope(certificate_fingerprint(certificate), ciphertext)


def unwrap_data_key(envelope: RewrapEnvelope, private_key: rsa.RSAPrivateKey) -> bytes:
    data_key = private_key.decrypt(
        envelope.ciphertext,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    if len(data_key) != 32:
        raise ValueError("Unwrapped ZLAPI data key must be exactly 32 bytes")
    return data_key
