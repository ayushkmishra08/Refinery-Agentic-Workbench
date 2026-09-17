"""Time-based one-time passwords (RFC 6238), for the second factor on privileged accounts.

A password proves you know a secret. It does not prove the person typing it is the person the
account belongs to — a leaked or shoulder-surfed password is indistinguishable from the real
thing. A second factor closes that gap by requiring something the account holder *has*: an
authenticator app holding a shared secret, producing a six-digit code that changes every thirty
seconds and is useless a minute later.

This is the standard algorithm, so any authenticator works — Google Authenticator, Authy, 1Password,
`oathtool`. Nothing is sent anywhere: the code is derived from the shared secret and the clock, so
it needs no email, no SMS and no network.

Three details that matter in practice:

**A window, not an instant.** Clocks drift. A code from the previous or next thirty-second step is
accepted (``VALID_WINDOW``), which is ninety seconds of tolerance in total — the usual trade-off.

**Codes cannot be replayed.** A code stays valid for its whole step, so an attacker who sees one
could otherwise reuse it within that window. Every accepted code is remembered until its step
expires and a second use is refused.

**Comparison is constant-time.** ``hmac.compare_digest`` on the digits, so a wrong code costs the
same as a right one.

Enrolment hands out the secret once, as an ``otpauth://`` URI for a QR code. The secret is stored
because verification needs it — unlike a password, a TOTP secret is necessarily reversible. It is
kept in the same file as the credentials, which is why that file is the one to protect.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

DIGITS = 6
STEP_SECONDS = 30
VALID_WINDOW = 1          # accept the previous and next step: ±30 s of clock drift
SECRET_BYTES = 20         # 160 bits, the RFC 4226 recommendation


class TotpError(RuntimeError):
    """The code was refused. The message is safe to show a user."""


def new_secret() -> str:
    """A fresh base32 secret, in the form authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFF_FFFF
    return str(truncated % (10 ** DIGITS)).zfill(DIGITS)


def code_now(secret: str, at: float | None = None) -> str:
    """The code an authenticator is showing right now. Used by enrolment checks and demo mode."""
    return _code_at(secret, int((at if at is not None else time.time()) // STEP_SECONDS))


def seconds_remaining(at: float | None = None) -> int:
    """How long the current code stays valid — what the UI counts down."""
    now = at if at is not None else time.time()
    return STEP_SECONDS - int(now % STEP_SECONDS)


def verify(secret: str, code: str, *, at: float | None = None, used_counters: set[int] | None = None) -> int:
    """Check a code and return the counter it matched, so the caller can burn it.

    Raises ``TotpError`` when the code is malformed, wrong, or already spent. Returning the
    counter rather than ``True`` is what makes replay protection possible: the caller records it
    and refuses the same step twice.
    """
    digits = (code or "").strip().replace(" ", "").replace("-", "")
    if not digits.isdigit() or len(digits) != DIGITS:
        raise TotpError(f"A {DIGITS}-digit code from your authenticator app is required.")
    now = at if at is not None else time.time()
    current = int(now // STEP_SECONDS)
    for drift in range(-VALID_WINDOW, VALID_WINDOW + 1):
        counter = current + drift
        if hmac.compare_digest(_code_at(secret, counter), digits):
            if used_counters is not None and counter in used_counters:
                raise TotpError("That code has already been used. Wait for the next one.")
            return counter
    raise TotpError("That code is not correct. Check your authenticator app and try again.")


def provisioning_uri(secret: str, *, username: str, issuer: str = "MRPL AI Workstation") -> str:
    """The ``otpauth://`` URI an authenticator reads from a QR code."""
    label = urllib.parse.quote(f"{issuer}:{username}")
    query = urllib.parse.urlencode({
        "secret": secret, "issuer": issuer, "algorithm": "SHA1",
        "digits": DIGITS, "period": STEP_SECONDS,
    })
    return f"otpauth://totp/{label}?{query}"
