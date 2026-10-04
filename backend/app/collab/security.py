"""Passwords and session tokens.

Passwords are never stored: only a salted scrypt hash "scrypt$n$r$p$salt$hash" (stdlib hashlib).
Session tokens are random; the database keeps only their SHA-256, so a leaked database does not
give working sessions.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

N, R, P, DKLEN = 2 ** 14, 8, 1, 32


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P, dklen=DKLEN)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r),
                             p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


# a valid hash of a random password: login of an unknown user costs the same time as a wrong password
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O, 1/I


def public_id() -> str:
    """Short id a person can dictate: 8 characters, e.g. 7KQ2M9XA (shown as #7KQ2-M9XA)."""
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(8))
