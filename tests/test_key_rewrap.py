from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from control_center.key_rewrap import RewrapEnvelope, unwrap_data_key, wrap_data_key


def certificate(private_key: rsa.RSAPrivateKey) -> x509.Certificate:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ZLAPI rewrap test")])
    now = datetime.now(UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )


def test_rewrap_envelope_round_trip_without_exposing_key() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    data_key = b"K" * 32
    envelope = wrap_data_key(data_key, certificate(private_key))
    encoded = envelope.to_bytes()

    assert data_key not in encoded
    assert unwrap_data_key(RewrapEnvelope.from_bytes(encoded), private_key) == data_key


def test_rewrap_rejects_wrong_key_size_and_tampered_envelope() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    cert = certificate(private_key)
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        wrap_data_key(b"short", cert)

    envelope = wrap_data_key(b"A" * 32, cert)
    damaged = RewrapEnvelope(envelope.certificate_sha256, envelope.ciphertext[:-1] + b"0")
    with pytest.raises(ValueError):
        unwrap_data_key(damaged, private_key)


def test_rewrap_rejects_rsa_keys_below_3072_bits() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ValueError, match="at least 3072 bits"):
        wrap_data_key(b"A" * 32, certificate(private_key))
