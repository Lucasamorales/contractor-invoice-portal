"""Bank-field encryption (R-SEC-03). No database, no network."""

from __future__ import annotations

import pytest

from modules.invoices.config import ConfigError, Settings
from modules.invoices.services.crypto import (
    Cipher,
    CryptoError,
    field_aad,
    generate_key,
)

PERSON = "8c1f1b2e-0000-0000-0000-000000000001"
OTHER_PERSON = "8c1f1b2e-0000-0000-0000-000000000002"


def cipher(key_id: str = "v1") -> Cipher:
    settings = Settings(
        bank_encryption_key=generate_key(),
        bank_encryption_key_id=key_id,
    )
    return Cipher.from_settings(settings)


def test_round_trip() -> None:
    box = cipher()
    aad = field_aad(PERSON, "account_number")
    stored = box.encrypt("12345678", aad=aad)
    assert box.decrypt(stored, aad=aad) == "12345678"


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    box = cipher()
    stored = box.encrypt("12345678", aad=field_aad(PERSON, "account_number"))
    assert "12345678" not in stored


def test_same_value_encrypts_differently_each_time() -> None:
    # A fresh nonce per write means two people with the same sort code do not
    # produce identical ciphertext, which would leak that they match.
    box = cipher()
    aad = field_aad(PERSON, "sort_code")
    assert box.encrypt("230801", aad=aad) != box.encrypt("230801", aad=aad)


def test_ciphertext_is_bound_to_the_person() -> None:
    # Lifting a row from one person onto another must fail, not silently pay
    # the wrong account.
    box = cipher()
    stored = box.encrypt("12345678", aad=field_aad(PERSON, "account_number"))
    with pytest.raises(CryptoError):
        box.decrypt(stored, aad=field_aad(OTHER_PERSON, "account_number"))


def test_ciphertext_is_bound_to_the_field() -> None:
    box = cipher()
    stored = box.encrypt("12345678", aad=field_aad(PERSON, "account_number"))
    with pytest.raises(CryptoError):
        box.decrypt(stored, aad=field_aad(PERSON, "iban"))


def test_tampering_is_detected() -> None:
    box = cipher()
    aad = field_aad(PERSON, "iban")
    stored = box.encrypt("GB82WEST12345698765432", aad=aad)
    version, key_id, nonce, blob = stored.split(".")
    tampered = ".".join([version, key_id, nonce, blob[:-4] + "AAAA"])
    with pytest.raises(CryptoError):
        box.decrypt(tampered, aad=aad)


def test_key_id_travels_with_the_ciphertext() -> None:
    # R-SEC-03: rotation must be possible, so the writer's key id is readable
    # without holding the key.
    box = cipher(key_id="v7")
    stored = box.encrypt("230801", aad=field_aad(PERSON, "sort_code"))
    assert stored.split(".")[1] == "v7"


def test_a_different_key_cannot_decrypt() -> None:
    stored = cipher().encrypt("230801", aad=field_aad(PERSON, "sort_code"))
    with pytest.raises(CryptoError):
        cipher().decrypt(stored, aad=field_aad(PERSON, "sort_code"))


def test_error_never_quotes_the_value() -> None:
    box = cipher()
    stored = box.encrypt("12345678", aad=field_aad(PERSON, "account_number"))
    with pytest.raises(CryptoError) as caught:
        box.decrypt(stored, aad=field_aad(OTHER_PERSON, "account_number"))
    assert "12345678" not in str(caught.value)


def test_empty_value_is_refused() -> None:
    with pytest.raises(CryptoError):
        cipher().encrypt("", aad=field_aad(PERSON, "iban"))


def test_malformed_stored_value_is_refused() -> None:
    with pytest.raises(CryptoError):
        cipher().decrypt("not-a-ciphertext", aad=field_aad(PERSON, "iban"))


@pytest.mark.parametrize("key", ["", "not-base64!!", "c2hvcnQ="])
def test_bad_keys_are_refused_at_startup(key: str) -> None:
    with pytest.raises(ConfigError):
        Cipher.from_settings(Settings(bank_encryption_key=key))


def test_generated_key_is_the_right_size() -> None:
    import base64

    assert len(base64.b64decode(generate_key())) == 32
