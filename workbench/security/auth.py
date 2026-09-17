"""Credentials, lockout and bearer tokens.

Passwords are never stored: each credential holds a random 16-byte salt and a
PBKDF2-HMAC-SHA256 digest over 240 000 iterations, and verification is a constant-time
compare. A wrong password costs the same time as a right one, and five wrong ones inside the
lockout window close the account for fifteen minutes.

On first run the store seeds one account per role — ``admin``, ``manager`` and ``user`` — so the
workbench is usable immediately. Their passwords come from ``RWB_ADMIN_PASSWORD``,
``RWB_MANAGER_PASSWORD`` and ``RWB_USER_PASSWORD`` when those are set, and otherwise from
``SEED_ACCOUNTS`` below, in which case each account is marked ``must_change`` and every login
says so out loud.

A successful login mints a ``SessionToken``: 32 random bytes, stored by SHA-256 digest only,
valid for eight hours. Handing the token back is what proves the role on later requests.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

from pydantic import BaseModel, Field

from workbench.security import totp
from workbench.security.roles import Role, level_of, readable_tags
from workbench.security.totp import TotpError

logger = logging.getLogger(__name__)

PBKDF2_ITERATIONS = 240_000
SALT_BYTES = 16
TOKEN_BYTES = 32
DEFAULT_TOKEN_TTL_SECONDS = 8 * 3600
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60
FAILURE_WINDOW_SECONDS = 15 * 60

# The accounts created on first run: one per role, so every path through the system can be
# demonstrated without an administrator having to exist first. Each is flagged ``must_change``
# and the environment variable beside it replaces the password at seed time.
#   username   role            default password   environment override
SEED_ACCOUNTS: list[tuple[str, Role, str, str]] = [
    ("admin",   Role.ADMIN,   "Admin#2026",   "RWB_ADMIN_PASSWORD"),
    ("manager", Role.MANAGER, "Manager#2026", "RWB_MANAGER_PASSWORD"),
    ("user",    Role.USER,    "User#2026",    "RWB_USER_PASSWORD"),
]


class AuthError(RuntimeError):
    """Login refused. The message is safe to show: it never says which half was wrong."""

    def __init__(self, message: str, *, locked_until: float | None = None) -> None:
        super().__init__(message)
        self.locked_until = locked_until


class MfaRequired(RuntimeError):
    """The password was right and a second factor is still owed.

    Not an error in the usual sense — it is the middle of a two-step sign-in, and it deliberately
    carries no token. Raised only after the password has been verified, so it never doubles as an
    oracle for which accounts exist.
    """

    def __init__(self, *, username: str, role: str) -> None:
        super().__init__("An authenticator code is required to finish signing in.")
        self.username = username
        self.role = role


def hash_password(password: str, salt: bytes | None = None, iterations: int = PBKDF2_ITERATIONS) -> tuple[str, str, int]:
    """(salt_hex, digest_hex, iterations) for a password."""
    s = salt or secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), s, iterations)
    return s.hex(), digest.hex(), iterations


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Credential(BaseModel):
    username: str
    role: Role = Role.USER
    display_name: str = ""
    salt: str
    digest: str
    iterations: int = PBKDF2_ITERATIONS
    created: float = Field(default_factory=time.time)
    must_change: bool = Field(default=False, description="True while the account still carries its seeded password")
    failed_attempts: int = 0
    last_failure: float = 0.0
    locked_until: float = 0.0
    totp_secret: str | None = Field(default=None, description="Shared secret for the authenticator app; set at enrolment")
    totp_enrolled: float = 0.0
    totp_used_counters: list[int] = Field(default_factory=list, description="Recently accepted TOTP steps, so a code cannot be replayed")

    @property
    def mfa_enrolled(self) -> bool:
        return bool(self.totp_secret)

    def verify(self, password: str) -> bool:
        _s, digest, _i = hash_password(password, bytes.fromhex(self.salt), self.iterations)
        return hmac.compare_digest(digest, self.digest)


class SessionToken(BaseModel):
    token_digest: str
    username: str
    role: Role
    issued: float = Field(default_factory=time.time)
    expires: float = 0.0
    label: str = ""

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires


class Principal(BaseModel):
    """Who is asking. ``GUEST`` is the unauthenticated caller — it clears nothing but public."""
    username: str = "guest"
    role: Role = Role.GUEST
    display_name: str = ""
    authenticated: bool = False
    token: str | None = None              # the raw token, returned once at login and never stored
    expires: float = 0.0
    must_change_password: bool = False
    mfa_enrolled: bool = False
    mfa_satisfied: bool = False

    @property
    def level(self) -> int:
        return level_of(self.role)

    @property
    def tags(self) -> list[str]:
        """The document tags this principal may read — what the UI shows as their clearance."""
        return [t.value for t in readable_tags(self.role)]

    def describe(self) -> str:
        who = self.display_name or self.username
        return f"{who} ({self.role.value})" if self.authenticated else "unauthenticated"


GUEST = Principal()


class AuthService:
    """Credential store plus token registry, persisted under ``<security_dir>/``."""

    def __init__(self, security_dir: Path, *, token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
                 seed_default: bool = True) -> None:
        self.dir = Path(security_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.users_path = self.dir / "users.json"
        self.tokens_path = self.dir / "tokens.json"
        self.token_ttl = token_ttl_seconds
        self._users: dict[str, Credential] = {}
        self._tokens: dict[str, SessionToken] = {}
        # secrets minted by begin_enrolment but not yet proved; deliberately in memory only, so an
        # abandoned enrolment leaves nothing behind and cannot be activated by editing a file
        self._pending_enrolment: dict[str, str] = {}
        self._load()
        if seed_default and not self._users:
            self._seed_default()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        if self.users_path.exists():
            try:
                for row in json.loads(self.users_path.read_text(encoding="utf-8")).get("users", []):
                    c = Credential.model_validate(row)
                    self._users[c.username.lower()] = c
            except Exception as exc:
                logger.error("user store unreadable (%s); no account can log in until it is fixed", exc)
        if self.tokens_path.exists():
            try:
                now = time.time()
                for row in json.loads(self.tokens_path.read_text(encoding="utf-8")).get("tokens", []):
                    t = SessionToken.model_validate(row)
                    if not t.expired(now):
                        self._tokens[t.token_digest] = t
            except Exception as exc:
                logger.warning("token store unreadable (%s); everyone must log in again", exc)

    def _save_users(self) -> None:
        self._write(self.users_path, {"users": [c.model_dump(mode="json") for c in self._users.values()]})

    def _save_tokens(self) -> None:
        self._write(self.tokens_path, {"tokens": [t.model_dump(mode="json") for t in self._tokens.values()]})

    @staticmethod
    def _write(path: Path, payload: dict) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
        try:                                  # best effort on Windows; no-op where chmod is ignored
            path.chmod(0o600)
        except OSError:
            pass

    def _seed_default(self) -> None:
        """Create one account per role on first run."""
        from workbench.security.roles import title

        for username, role, default_password, env_var in SEED_ACCOUNTS:
            supplied = os.getenv(env_var)
            self.add_user(username, supplied or default_password, role, display_name=title(role),
                          must_change=not supplied)
        logger.info("seeded %d role accounts", len(SEED_ACCOUNTS))

    @staticmethod
    def seeded_accounts() -> list[dict]:
        """What the seeded accounts are, for the setup script and the documentation to print."""
        return [{"username": u, "role": r.value, "default_password": p, "env_override": e,
                 "from_environment": bool(os.getenv(e))}
                for u, r, p, e in SEED_ACCOUNTS]

    # ------------------------------------------------------------------ accounts
    def add_user(self, username: str, password: str, role: Role | str = Role.USER, *,
                 display_name: str = "", must_change: bool = False) -> Credential:
        salt, digest, iterations = hash_password(password)
        cred = Credential(username=username.lower(), role=Role(role), display_name=display_name or username,
                          salt=salt, digest=digest, iterations=iterations, must_change=must_change)
        self._users[cred.username] = cred
        self._save_users()
        return cred

    def set_password(self, username: str, password: str) -> Credential:
        cred = self._users[username.lower()]
        cred.salt, cred.digest, cred.iterations = hash_password(password)
        cred.must_change = False
        cred.failed_attempts = 0
        cred.locked_until = 0.0
        self._save_users()
        return cred

    def users(self) -> list[dict]:
        return [{"username": c.username, "role": c.role.value, "display_name": c.display_name,
                 "must_change": c.must_change, "locked": c.locked_until > time.time(),
                 "mfa_enrolled": c.mfa_enrolled} for c in self._users.values()]

    # ------------------------------------------------------------------ second factor
    def begin_enrolment(self, username: str, *, issuer: str = "MRPL AI Workstation") -> dict:
        """Mint a secret and hand back the QR payload. Not active until ``confirm_enrolment``.

        Two steps on purpose: an account whose secret were stored before the holder proved their
        app can produce a code would be locked out of its own second factor.
        """
        cred = self._users[username.lower()]
        secret = totp.new_secret()
        self._pending_enrolment[cred.username] = secret
        return {"username": cred.username, "secret": secret,
                "uri": totp.provisioning_uri(secret, username=cred.username, issuer=issuer),
                "digits": totp.DIGITS, "period": totp.STEP_SECONDS}

    def confirm_enrolment(self, username: str, code: str) -> Credential:
        """Activate the pending secret once the holder proves the app is producing its codes."""
        cred = self._users[username.lower()]
        secret = self._pending_enrolment.get(cred.username)
        if not secret:
            raise AuthError("Start enrolment first; there is no pending authenticator secret for this account.")
        counter = totp.verify(secret, code)                      # raises TotpError on a bad code
        cred.totp_secret = secret
        cred.totp_enrolled = time.time()
        cred.totp_used_counters = [counter]
        self._pending_enrolment.pop(cred.username, None)
        self._save_users()
        return cred

    def disable_mfa(self, username: str) -> Credential:
        cred = self._users[username.lower()]
        cred.totp_secret = None
        cred.totp_enrolled = 0.0
        cred.totp_used_counters = []
        self._save_users()
        return cred

    def verify_totp(self, username: str, code: str, *, at: float | None = None) -> None:
        """Check a code for an enrolled account and burn the step it matched.

        ``at`` overrides the clock; it exists so the tests can cross a step boundary without
        sleeping for thirty seconds, and is never set by the API.
        """
        cred = self._users[username.lower()]
        if not cred.totp_secret:
            raise AuthError("This account has no authenticator enrolled.")
        now = at if at is not None else time.time()
        counter = totp.verify(cred.totp_secret, code, at=now, used_counters=set(cred.totp_used_counters))
        # keep only steps that could still be replayed; the list never grows
        floor = int(now // totp.STEP_SECONDS) - totp.VALID_WINDOW
        cred.totp_used_counters = [c for c in [*cred.totp_used_counters, counter] if c >= floor]
        self._save_users()

    # ------------------------------------------------------------------ login
    def authenticate(self, username: str, password: str, *, label: str = "") -> Principal:
        """Verify a password and mint a token. Raises AuthError with a deliberately vague message."""
        now = time.time()
        cred = self._users.get((username or "").lower())
        if cred is None:
            # spend comparable time so a missing account is not distinguishable by timing
            hash_password(password or "")
            raise AuthError("Username or password is not correct.")
        if cred.locked_until > now:
            mins = int((cred.locked_until - now) // 60) + 1
            raise AuthError(f"This account is locked for another {mins} minute(s) after repeated failures.",
                            locked_until=cred.locked_until)
        if not cred.verify(password or ""):
            if now - cred.last_failure > FAILURE_WINDOW_SECONDS:
                cred.failed_attempts = 0
            cred.failed_attempts += 1
            cred.last_failure = now
            remaining = MAX_FAILED_ATTEMPTS - cred.failed_attempts
            if cred.failed_attempts >= MAX_FAILED_ATTEMPTS:
                cred.locked_until = now + LOCKOUT_SECONDS
                cred.failed_attempts = 0
                self._save_users()
                raise AuthError(f"Too many failed attempts; the account is locked for {LOCKOUT_SECONDS // 60} minutes.",
                                locked_until=cred.locked_until)
            self._save_users()
            raise AuthError(f"Username or password is not correct. {remaining} attempt(s) left before lockout.")
        cred.failed_attempts = 0
        cred.locked_until = 0.0
        self._save_users()
        return self._mint(cred, label=label)

    def authenticate_with_mfa(self, username: str, password: str, *, code: str | None = None,
                              label: str = "", required_roles: list[str] | None = None,
                              at: float | None = None) -> Principal:
        """Password first, then the authenticator code when the role requires one.

        The password is always checked first, so a wrong password never reveals whether the
        account has a second factor, and the lockout counter still governs. Only once the password
        is right does the second factor come into it:

        * role does not require MFA, or the account is not enrolled -> a token, as before;
        * role requires it and the account is enrolled, no code given -> ``MfaRequired``;
        * code given -> verified and burned, then a token.

        ``MfaRequired`` carries no token. There is no half-signed-in state to leak: a caller who
        stops here holds nothing.
        """
        principal = self.authenticate(username, password, label=label)   # raises on a bad password
        cred = self._users[principal.username]
        needs = (cred.role.value in (required_roles or []))
        principal.mfa_enrolled = cred.mfa_enrolled
        if not needs or not cred.mfa_enrolled:
            principal.mfa_satisfied = not needs
            return principal
        if not code:
            self.revoke(principal.token)          # the token minted a moment ago is not earned yet
            raise MfaRequired(username=cred.username, role=cred.role.value)
        try:
            self.verify_totp(cred.username, code, at=at)
        except (TotpError, AuthError):
            self.revoke(principal.token)
            raise
        principal.mfa_satisfied = True
        return principal

    def _mint(self, cred: Credential, *, label: str = "") -> Principal:
        raw = secrets.token_urlsafe(TOKEN_BYTES)
        st = SessionToken(token_digest=_token_digest(raw), username=cred.username, role=cred.role,
                          expires=time.time() + self.token_ttl, label=label)
        self._tokens[st.token_digest] = st
        self._prune()
        self._save_tokens()
        return Principal(username=cred.username, role=cred.role, display_name=cred.display_name, authenticated=True,
                         token=raw, expires=st.expires, must_change_password=cred.must_change)

    # ------------------------------------------------------------------ tokens
    def principal_for(self, token: str | None) -> Principal:
        """The principal a bearer token names, or GUEST when it is absent, unknown or expired."""
        if not token:
            return GUEST
        st = self._tokens.get(_token_digest(token))
        if st is None or st.expired():
            if st is not None:
                self._tokens.pop(st.token_digest, None)
                self._save_tokens()
            return GUEST
        cred = self._users.get(st.username)
        if cred is None or cred.role != st.role:      # role changed or account removed since the token was minted
            self.revoke(token)
            return GUEST
        return Principal(username=st.username, role=st.role, display_name=cred.display_name, authenticated=True,
                         token=token, expires=st.expires, must_change_password=cred.must_change)

    def revoke(self, token: str | None) -> bool:
        if not token:
            return False
        removed = self._tokens.pop(_token_digest(token), None) is not None
        if removed:
            self._save_tokens()
        return removed

    def revoke_all(self, username: str | None = None) -> int:
        before = len(self._tokens)
        self._tokens = {d: t for d, t in self._tokens.items() if username and t.username != username.lower()}
        self._save_tokens()
        return before - len(self._tokens)

    def _prune(self) -> None:
        now = time.time()
        self._tokens = {d: t for d, t in self._tokens.items() if not t.expired(now)}
