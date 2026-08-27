"""Encryption for the secrets Hooklab stores on behalf of its users.

Only one thing is encrypted today -- a webhook signing secret -- but it is the
worst thing in the database to leak. Holding it lets an attacker *forge* webhooks
that the customer's own systems will accept as genuine, which is a good deal
worse than reading past traffic.

**AES-GCM, not plain AES.** GCM is authenticated: decryption fails loudly if the
ciphertext was altered. Unauthenticated encryption lets an attacker with write
access to the database flip bits and have the result decrypt into something else
without anyone noticing.

**The key is derived, not used directly.** `SECRET_KEY` may end up serving other
purposes -- signing cookies, tokens -- and reusing one key across purposes means a
weakness in any of them compromises all of them. HKDF with a fixed label keeps
this key separate from every future one.

**Rotating `SECRET_KEY` makes every stored secret unreadable.** That is inherent,
not a bug: the key is the only thing standing between a database dump and the
secrets. Decryption failure is reported as such so a user can simply set the
secret again, rather than being told the signature is invalid.
"""

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings

# 96 bits is the nonce size AES-GCM is specified and optimised for. Longer or
# shorter values get hashed internally, losing the guarantee that distinct
# nonces stay distinct.
NONCE_BYTES = 12
KEY_BYTES = 32  # AES-256

# Domain separation label. Changing it invalidates every stored ciphertext, so it
# is as fixed as the key itself.
HKDF_INFO = b"hooklab.signature-secret.v1"


class UndecryptableSecret(Exception):
    """The stored ciphertext could not be opened with the current key.

    Almost always means `SECRET_KEY` changed. The alternative -- that someone
    tampered with the row -- is exactly what GCM's authentication tag is there to
    catch, and both deserve the same answer: refuse, do not guess.
    """


def _key() -> bytes:
    """Derive the AES key from the application secret.

    No salt: it would have to be stored next to the ciphertext to be usable, and
    the input here is already a high-entropy random key rather than a password.
    HKDF's role in this case is domain separation and length adjustment, not
    stretching a weak input.
    """
    settings = get_settings()
    return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=HKDF_INFO).derive(
        settings.secret_key.encode()
    )


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage, returning base64 text.

    A fresh random nonce every time, prepended to the ciphertext. Reusing a nonce
    with the same key is the one catastrophic mistake in GCM: two messages under
    one nonce leak their XOR and destroy the authentication guarantee outright.
    """
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(_key()).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + sealed).decode("ascii")


def decrypt(stored: str) -> str:
    """Recover a secret, or refuse.

    Raises `UndecryptableSecret` for anything that is not intact ciphertext under
    the current key -- wrong key, tampered bytes, truncated value or garbage.
    Callers treat it as "this secret is unusable", never as "verification failed".
    """
    try:
        raw = base64.b64decode(stored, validate=True)
    except (ValueError, TypeError) as exc:
        raise UndecryptableSecret("stored value is not valid base64") from exc

    if len(raw) <= NONCE_BYTES:
        raise UndecryptableSecret("stored value is too short to contain a nonce")

    nonce, sealed = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
    try:
        return AESGCM(_key()).decrypt(nonce, sealed, None).decode()
    except (InvalidTag, UnicodeDecodeError) as exc:
        raise UndecryptableSecret("wrong key, or the ciphertext was altered") from exc
