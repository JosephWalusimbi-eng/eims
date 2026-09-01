"""
Application-layer encryption for personally identifying fields.

EIMS replicates child records onto facility devices, so a stolen or lost device
is a realistic adversary (paper Sec. VI threat model, T1). Column-level
encryption keeps the plaintext out of both the central database and the local
replica: an attacker with the database file, a backup, or a dump obtains
ciphertext only.

Construction: AES-256-GCM with a 96-bit random nonce per value and the field's
fully-qualified name as additional authenticated data, so a ciphertext cannot be
moved between columns. Values are stored base64-encoded with a version prefix
so the scheme can be rotated without a data migration.

Key material comes from EIMS_FIELD_KEY (base64, 32 bytes). Key management is
operational, not hardware-backed; this is stated as a residual risk in the paper
rather than claimed as solved.
"""
from __future__ import annotations

import base64
import os

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - dependency is declared in requirements
    AESGCM = None

PREFIX = "v1:"
_NONCE_BYTES = 12


def generate_key() -> str:
    """Return a fresh base64 key suitable for EIMS_FIELD_KEY."""
    return base64.b64encode(os.urandom(32)).decode("ascii")


def _key() -> bytes:
    raw = getattr(settings, "EIMS_FIELD_KEY", "") or ""
    if not raw:
        raise ImproperlyConfigured(
            "EIMS_FIELD_KEY is not set. Generate one with "
            "`python manage.py rotate_field_key --new` and place it in .env."
        )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise ImproperlyConfigured("EIMS_FIELD_KEY must decode to 32 bytes (AES-256).")
    return key


def encrypt(plaintext: str, aad: str) -> str:
    if plaintext is None or plaintext == "":
        return ""
    if AESGCM is None:
        raise ImproperlyConfigured("cryptography is required for field encryption.")
    nonce = os.urandom(_NONCE_BYTES)
    blob = AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
    return PREFIX + base64.b64encode(nonce + blob).decode("ascii")


def decrypt(stored: str, aad: str) -> str:
    if not stored:
        return ""
    if not stored.startswith(PREFIX):
        # Legacy plaintext row written before encryption was enabled. Returned
        # as-is so an existing deployment keeps working while it is migrated.
        return stored
    raw = base64.b64decode(stored[len(PREFIX):])
    nonce, blob = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    return AESGCM(_key()).decrypt(nonce, blob, aad.encode("utf-8")).decode("utf-8")


class EncryptedTextMixin:
    """Transparently encrypts on write and decrypts on read.

    Stored as text, so the column is no longer searchable or sortable in SQL.
    That is the intended trade: identifying fields are looked up by the child's
    UUID or by the caregiver's blind index, never by substring.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 512)
        super().__init__(*args, **kwargs)

    def _aad(self) -> str:
        return f"{self.model._meta.label_lower}.{self.name}"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return ""
        return encrypt(str(value), self._aad())

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return ""
        return decrypt(value, self._aad())

    def to_python(self, value):
        if value in (None, ""):
            return ""
        if isinstance(value, str) and value.startswith(PREFIX):
            return decrypt(value, self._aad())
        return value


class EncryptedCharField(EncryptedTextMixin, models.CharField):
    pass


class EncryptedEmailField(EncryptedTextMixin, models.CharField):
    pass


def blind_index(value: str, aad: str = "phone") -> str:
    """Deterministic HMAC of a value, for equality lookup on an encrypted column.

    Reveals equality between records (two children sharing a caregiver phone
    hash alike) but not the value itself, which is the trade an offline-capable
    registry needs in order to deduplicate caregivers without decrypting.
    """
    import hmac
    import hashlib

    if not value:
        return ""
    normalised = "".join(ch for ch in str(value) if ch.isdigit() or ch == "+")
    return hmac.new(_key(), (aad + "|" + normalised).encode("utf-8"),
                    hashlib.sha256).hexdigest()
