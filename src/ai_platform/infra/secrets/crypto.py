"""Secret encryption — AES-256-GCM for API key storage."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ai_platform.config import get_settings

# Encryption key derived from APP_SECRET_KEY using SHA-256
# In production, use Vault/KMS instead of this local derivation
_NONCE_SIZE = 12  # 96 bits for AES-GCM


def _get_encryption_key() -> bytes:
    """Derive a 256-bit encryption key from the app secret."""
    settings = get_settings()
    return hashlib.sha256(settings.app_secret_key.encode()).digest()


def encrypt_secret(plaintext: str) -> str:
    """
    Encrypt a secret (API key) for database storage.

    Returns a base64-encoded string containing nonce + ciphertext.
    """
    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    nonce = secrets.token_bytes(_NONCE_SIZE)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

    # Combine nonce + ciphertext and base64 encode for DB storage
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode("ascii")


def decrypt_secret(encrypted: str) -> str:
    """
    Decrypt a secret read from the database.

    Input is the base64-encoded string from encrypt_secret().
    """
    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    combined = base64.b64decode(encrypted)

    nonce = combined[:_NONCE_SIZE]
    ciphertext = combined[_NONCE_SIZE:]
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


def mask_secret(secret: str) -> str:
    """
    Mask a secret for display — show only first 4 and last 4 characters.

    Example: "sk-abc123xyz789" → "sk-a...z789"
    """
    if len(secret) <= 10:
        return secret[:2] + "****" + secret[-2:]
    return secret[:4] + "..." + secret[-4:]
