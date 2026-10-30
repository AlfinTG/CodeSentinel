"""Cryptographic module for CodeSentinel database backup encryption and decryption.

Uses AES-256-GCM (Galois/Counter Mode) authenticated encryption with 96-bit (12-byte)
random initialization vectors (IVs) and 128-bit authentication tags to ensure both
data confidentiality and tamper-proof integrity.
"""

import base64
import os
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC_HEADER = b"CSB1"  # CodeSentinel Backup Format v1
NONCE_LENGTH = 12  # 96 bits for GCM
SALT_LENGTH = 16  # 128 bits for PBKDF2


class BackupCryptoError(Exception):
    """Base exception for backup cryptographic errors."""


def generate_backup_key() -> str:
    """Generate a cryptographically secure 256-bit (32-byte) AES key, base64-encoded."""
    raw_key = AESGCM.generate_key(bit_length=256)
    return base64.urlsafe_b64encode(raw_key).decode("utf-8")


def derive_key_from_passphrase(passphrase: str, salt: bytes) -> bytes:
    """Derive a 256-bit key from a passphrase using PBKDF2-HMAC-SHA256."""
    if not passphrase:
        raise BackupCryptoError("Passphrase cannot be empty")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def parse_key(key_input: str | bytes) -> bytes:
    """Parse and validate a 256-bit key from bytes, hex string, or base64 string."""
    if isinstance(key_input, str):
        key_input = key_input.strip()
        # Try base64 (with automatic padding normalization)
        try:
            padded_key = key_input + "=" * (-len(key_input) % 4)
            decoded = base64.urlsafe_b64decode(padded_key)
            if len(decoded) == 32:
                return decoded
        except Exception:
            pass
        # Try hex
        try:
            decoded = bytes.fromhex(key_input)
            if len(decoded) == 32:
                return decoded
        except Exception:
            pass
        # Raw string of exactly 32 bytes
        raw_bytes = key_input.encode("utf-8")
        if len(raw_bytes) == 32:
            return raw_bytes
        raise BackupCryptoError(
            f"Invalid encryption key length: got {len(raw_bytes)} bytes. "
            "Key must be exactly 32 bytes (256 bits), hex-encoded (64 chars), or base64-encoded."
        )

    if isinstance(key_input, bytes):
        if len(key_input) == 32:
            return key_input
        raise BackupCryptoError(f"Invalid encryption key byte length: got {len(key_input)}, expected 32.")

    raise BackupCryptoError("Encryption key must be provided as string or bytes.")


def encrypt_bytes(data: bytes, key: bytes | str) -> bytes:
    """Encrypt byte payload using AES-256-GCM.

    Format: [MAGIC: 4 bytes] + [NONCE: 12 bytes] + [CIPHERTEXT + 16-byte TAG]
    """
    valid_key = parse_key(key)
    aesgcm = AESGCM(valid_key)
    nonce = os.urandom(NONCE_LENGTH)
    ciphertext = aesgcm.encrypt(nonce, data, associated_data=MAGIC_HEADER)
    return MAGIC_HEADER + nonce + ciphertext


def decrypt_bytes(encrypted_data: bytes, key: bytes | str) -> bytes:
    """Decrypt byte payload using AES-256-GCM and verify integrity tag."""
    if len(encrypted_data) < len(MAGIC_HEADER) + NONCE_LENGTH + 16:
        raise BackupCryptoError("Encrypted payload is corrupted or too short.")

    header = encrypted_data[: len(MAGIC_HEADER)]
    if header != MAGIC_HEADER:
        raise BackupCryptoError(f"Invalid backup file format: unknown magic header {header!r}.")

    valid_key = parse_key(key)
    nonce = encrypted_data[len(MAGIC_HEADER) : len(MAGIC_HEADER) + NONCE_LENGTH]
    ciphertext = encrypted_data[len(MAGIC_HEADER) + NONCE_LENGTH :]

    aesgcm = AESGCM(valid_key)
    try:
        return aesgcm.decrypt(nonce, ciphertext, associated_data=MAGIC_HEADER)
    except InvalidTag as exc:
        raise BackupCryptoError(
            "Decryption integrity check failed: invalid key or corrupted/tampered ciphertext."
        ) from exc


def encrypt_file(source_path: str | Path, dest_path: str | Path, key: bytes | str) -> Path:
    """Encrypt a file from source_path to dest_path using AES-256-GCM."""
    src = Path(source_path)
    dst = Path(dest_path)
    if not src.exists():
        raise BackupCryptoError(f"Source file not found: {src}")

    raw_data = src.read_bytes()
    encrypted = encrypt_bytes(raw_data, key)
    dst.write_bytes(encrypted)
    return dst


def decrypt_file(source_path: str | Path, dest_path: str | Path, key: bytes | str) -> Path:
    """Decrypt an encrypted file from source_path to dest_path using AES-256-GCM."""
    src = Path(source_path)
    dst = Path(dest_path)
    if not src.exists():
        raise BackupCryptoError(f"Source file not found: {src}")

    encrypted_data = src.read_bytes()
    decrypted = decrypt_bytes(encrypted_data, key)
    dst.write_bytes(decrypted)
    return dst


def main():
    """CLI helper for backup encryption and decryption."""
    if len(sys.argv) < 2:
        print("Usage: python -m app.services.backup.crypto [generate-key | encrypt | decrypt] [args...]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "generate-key":
        print(generate_backup_key())
        return

    if cmd in ("encrypt", "decrypt"):
        if len(sys.argv) < 5:
            print(f"Usage: python -m app.services.backup.crypto {cmd} <source_file> <dest_file> <key_or_env_var>")
            sys.exit(1)
        src = sys.argv[2]
        dst = sys.argv[3]
        key_arg = sys.argv[4]

        # Check if key_arg is an environment variable name
        key_val = os.environ.get(key_arg, key_arg)
        try:
            if cmd == "encrypt":
                encrypt_file(src, dst, key_val)
                print(f"[OK] Encrypted {src} -> {dst}")
            else:
                decrypt_file(src, dst, key_val)
                print(f"[OK] Decrypted {src} -> {dst}")
        except Exception as exc:
            print(f"[ERROR] Cryptographic operation failed: {exc}", file=sys.stderr)
            sys.exit(2)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()

