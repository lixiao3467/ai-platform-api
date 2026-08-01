"""Tests for secret encryption/decryption."""

from ai_platform.infra.secrets.crypto import decrypt_secret, encrypt_secret, mask_secret


def test_encrypt_decrypt_roundtrip() -> None:
    """Encrypted text should decrypt back to original."""
    original = "sk-abc123xyz789"
    encrypted = encrypt_secret(original)
    assert encrypted != original  # must be different
    assert decrypt_secret(encrypted) == original


def test_different_encryptions_for_same_input() -> None:
    """Same plaintext should produce different ciphertexts (random nonce)."""
    original = "sk-test-key"
    e1 = encrypt_secret(original)
    e2 = encrypt_secret(original)
    assert e1 != e2  # different nonces
    assert decrypt_secret(e1) == original
    assert decrypt_secret(e2) == original


def test_encrypt_empty_string() -> None:
    encrypted = encrypt_secret("")
    assert decrypt_secret(encrypted) == ""


def test_encrypt_long_key() -> None:
    """Should handle long API keys."""
    original = "sk-" + "a" * 200
    encrypted = encrypt_secret(original)
    assert decrypt_secret(encrypted) == original


def test_encrypt_special_characters() -> None:
    """Should handle unicode and special characters."""
    original = "密钥-key-🔐-special!@#$%^&*()"
    encrypted = encrypt_secret(original)
    assert decrypt_secret(encrypted) == original


def test_mask_secret_long() -> None:
    assert mask_secret("sk-abc123xyz789") == "sk-a...z789"


def test_mask_secret_short() -> None:
    assert mask_secret("abc") == "ab****bc"


def test_mask_secret_medium() -> None:
    result = mask_secret("abcdefghijk")
    assert result == "abcd...hijk"
