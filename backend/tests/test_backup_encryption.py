"""Tests for CodeSentinel backup encryption and decryption (AES-256-GCM)."""

import tempfile
from pathlib import Path

import pytest

from app.services.backup.crypto import (
    BackupCryptoError,
    decrypt_bytes,
    decrypt_file,
    derive_key_from_passphrase,
    encrypt_bytes,
    encrypt_file,
    generate_backup_key,
    parse_key,
)


def test_generate_and_parse_backup_key():
    """Verify key generation produces valid 32-byte urlsafe base64 keys."""
    key_str = generate_backup_key()
    assert isinstance(key_str, str)
    key_bytes = parse_key(key_str)
    assert len(key_bytes) == 32


def test_parse_key_formats_and_invalid_lengths():
    """Verify parse_key accepts base64, hex, and raw 32-byte strings, and rejects invalid lengths."""
    # 32 raw bytes
    raw_32 = b"01234567890123456789012345678901"
    assert parse_key(raw_32) == raw_32

    # 64-char hex
    hex_key = raw_32.hex()
    assert parse_key(hex_key) == raw_32

    # Unpadded base64 key
    b64_key = generate_backup_key()
    unpadded_b64 = b64_key.rstrip("=")
    assert parse_key(unpadded_b64) == parse_key(b64_key)

    # Invalid short key
    with pytest.raises(BackupCryptoError) as exc_info:
        parse_key("too-short-key")
    assert "Invalid encryption key" in str(exc_info.value)
    # Ensure key itself is NOT printed in the exception
    assert "too-short-key" not in str(exc_info.value)


def test_derive_key_from_passphrase():
    """Verify PBKDF2 passphrase key derivation produces consistent 32-byte keys."""
    salt = b"test_salt_123456"
    key1 = derive_key_from_passphrase("super-secret-passphrase", salt)
    key2 = derive_key_from_passphrase("super-secret-passphrase", salt)
    key_diff = derive_key_from_passphrase("different-passphrase", salt)

    assert len(key1) == 32
    assert key1 == key2
    assert key1 != key_diff

    with pytest.raises(BackupCryptoError):
        derive_key_from_passphrase("", salt)


def test_aes_gcm_encrypt_decrypt_bytes_roundtrip():
    """Verify AES-256-GCM authenticated encryption roundtrip on byte payloads."""
    key = generate_backup_key()
    original_data = b"PostgreSQL database binary dump content with secret tables and rows."

    encrypted = encrypt_bytes(original_data, key)
    assert encrypted.startswith(b"CSB1")
    assert encrypted != original_data

    decrypted = decrypt_bytes(encrypted, key)
    assert decrypted == original_data


def test_tampered_ciphertext_fails_integrity_verification():
    """Verify AES-256-GCM authentication tag rejects tampered ciphertext or wrong keys."""
    key1 = generate_backup_key()
    key2 = generate_backup_key()
    original_data = b"Sensitive production financial audit records."

    encrypted = encrypt_bytes(original_data, key1)

    # 1. Decrypting with wrong key must fail
    with pytest.raises(BackupCryptoError) as exc_info:
        decrypt_bytes(encrypted, key2)
    assert "integrity check failed" in str(exc_info.value)

    # 2. Tampering with 1 byte in the ciphertext body must fail tag verification
    tampered = bytearray(encrypted)
    tampered[-5] ^= 0x55
    with pytest.raises(BackupCryptoError) as exc_info:
        decrypt_bytes(bytes(tampered), key1)
    assert "integrity check failed" in str(exc_info.value)


def test_encrypt_and_decrypt_file_roundtrip():
    """Verify end-to-end file encryption and decryption."""
    key = generate_backup_key()

    with tempfile.TemporaryDirectory() as temp_dir:
        src = Path(temp_dir) / "source_backup.dump"
        enc = Path(temp_dir) / "source_backup.dump.enc"
        dec = Path(temp_dir) / "restored_backup.dump"

        test_payload = b"DATABASE_DUMP_STREAM_" * 1024
        src.write_bytes(test_payload)

        encrypt_file(src, enc, key)
        assert enc.exists()
        assert enc.stat().st_size > len(test_payload)

        decrypt_file(enc, dec, key)
        assert dec.exists()
        assert dec.read_bytes() == test_payload
