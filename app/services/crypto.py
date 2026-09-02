"""Encryption of the bank columns (R-SEC-03, CLAUDE.md §2.5).

``account_number``, ``sort_code``, ``iban`` and ``swift_bic`` are encrypted in
the application before they are inserted, so the database holds only
ciphertext. This is deliberate: the database is a third party, and a breach
there must not yield anything payable.

AES-256-GCM. The key comes from Secret Manager; the key id travels with every
ciphertext so a rotation does not require re-encrypting everything at once.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import ConfigError, Settings

NONCE_BYTES = 12
KEY_BYTES = 32
_SEPARATOR = "."
_VERSION = "1"


class CryptoError(RuntimeError):
    """Encryption or decryption failed. Never carries the plaintext."""


@dataclass(frozen=True, slots=True)
class Cipher:
    """A key and its id. Built once per process from settings."""

    key_id: str
    _key: bytes

    @classmethod
    def from_settings(cls, settings: Settings) -> Cipher:
        raw = settings.bank_encryption_key.get_secret_value()
        if not raw:
            raise ConfigError("bank_encryption_key is not configured")
        try:
            key = base64.b64decode(raw, validate=True)
        except (ValueError, TypeError) as exc:
            raise ConfigError("bank_encryption_key is not valid base64") from exc
        if len(key) != KEY_BYTES:
            raise ConfigError(
                f"bank_encryption_key must decode to {KEY_BYTES} bytes, got {len(key)}"
            )
        return cls(key_id=settings.bank_encryption_key_id, _key=key)

    def encrypt(self, plaintext: str, *, aad: str) -> str:
        """Encrypt one field.

        ``aad`` binds the ciphertext to where it belongs — the person's id and
        the field name — so a row cannot be lifted from one person and pasted
        onto another without the decryption failing.

        Returns ``1.<key_id>.<nonce>.<ciphertext>``, all base64, so the column
        stays plain text and the key id is readable without the key.
        """
        if not plaintext:
            raise CryptoError("refusing to encrypt an empty value")
        nonce = os.urandom(NONCE_BYTES)
        blob = AESGCM(self._key).encrypt(
            nonce, plaintext.encode("utf-8"), aad.encode("utf-8")
        )
        return _SEPARATOR.join([_VERSION, self.key_id, _b64(nonce), _b64(blob)])

    def decrypt(self, stored: str, *, aad: str) -> str:
        """Decrypt one field, verifying it belongs where it was found."""
        try:
            version, key_id, nonce_b64, blob_b64 = stored.split(_SEPARATOR)
        except ValueError as exc:
            raise CryptoError("stored value is not in the expected format") from exc
        if version != _VERSION:
            raise CryptoError(f"unsupported ciphertext version {version}")
        if key_id != self.key_id:
            raise CryptoError(
                f"ciphertext was written with key {key_id}, this process holds "
                f"{self.key_id}"
            )
        try:
            plaintext = AESGCM(self._key).decrypt(
                _unb64(nonce_b64), _unb64(blob_b64), aad.encode("utf-8")
            )
        except (InvalidTag, ValueError) as exc:
            # The message never quotes the value: it would be the plaintext or
            # a decryption oracle, and neither belongs in a log (R-SEC-05).
            raise CryptoError("could not decrypt the stored bank field") from exc
        return plaintext.decode("utf-8")


def field_aad(person_id: str, field_name: str) -> str:
    """The associated data binding a ciphertext to a person and a column."""
    return f"portal:bank:{person_id}:{field_name}"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise CryptoError("stored value is not valid base64") from exc


def generate_key() -> str:
    """A fresh base64 AES-256 key, for provisioning a Secret Manager version.

    Printed once by an operator and pasted into Secret Manager; it is never
    written to a file by this application.
    """
    return _b64(os.urandom(KEY_BYTES))
