"""Tests for encryption of stored secrets."""

import base64

import pytest

from app.core import crypto
from app.core.config import get_settings

SECRET = "whsec_a_real_looking_stripe_secret"


def test_a_secret_survives_a_round_trip() -> None:
    assert crypto.decrypt(crypto.encrypt(SECRET)) == SECRET


def test_the_plaintext_is_not_in_the_stored_value() -> None:
    """The obvious property, and the one worth asserting out loud."""
    stored = crypto.encrypt(SECRET)

    assert SECRET not in stored
    assert SECRET.encode() not in base64.b64decode(stored)


def test_encrypting_twice_gives_different_ciphertext() -> None:
    """A fresh nonce each time.

    Identical output would leak that two endpoints share a secret, and reusing a
    nonce under one key breaks GCM outright.
    """
    first, second = crypto.encrypt(SECRET), crypto.encrypt(SECRET)

    assert first != second
    assert crypto.decrypt(first) == crypto.decrypt(second) == SECRET


def test_tampering_is_detected() -> None:
    """What authenticated encryption buys over plain AES.

    Someone with write access to the database must not be able to alter a stored
    secret into a different valid one.
    """
    raw = bytearray(base64.b64decode(crypto.encrypt(SECRET)))
    raw[-1] ^= 0x01  # flip one bit of the tag
    tampered = base64.b64encode(bytes(raw)).decode()

    with pytest.raises(crypto.UndecryptableSecret):
        crypto.decrypt(tampered)


def test_a_flipped_ciphertext_bit_is_detected() -> None:
    raw = bytearray(base64.b64decode(crypto.encrypt(SECRET)))
    raw[crypto.NONCE_BYTES] ^= 0x01  # first byte of the ciphertext itself
    with pytest.raises(crypto.UndecryptableSecret):
        crypto.decrypt(base64.b64encode(bytes(raw)).decode())


def test_a_different_key_cannot_read_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rotating SECRET_KEY makes stored secrets unreadable, by design.

    It is the whole point: the key is what stands between a database dump and the
    secrets. The failure has to be explicit so the user is told to set the secret
    again, instead of being shown a bogus "invalid signature".
    """
    stored = crypto.encrypt(SECRET)

    settings = get_settings()
    monkeypatch.setattr(settings, "secret_key", "a-completely-different-key")

    with pytest.raises(crypto.UndecryptableSecret):
        crypto.decrypt(stored)


@pytest.mark.parametrize(
    "value",
    ["", "not base64 at all!!", "c2hvcnQ=", "AAAA"],
    ids=["empty", "not-base64", "too-short", "no-payload"],
)
def test_garbage_is_refused_rather_than_guessed(value: str) -> None:
    with pytest.raises(crypto.UndecryptableSecret):
        crypto.decrypt(value)
