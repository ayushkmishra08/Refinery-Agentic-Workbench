"""Asking for what you are not cleared for, and being given exactly that and nothing else.

A user asks a question the CDU manual answers. They are not cleared for the CDU manual, so they
are refused — and the refusal carries a way forward:

    1. REQUEST    The workbench raises an AccessRequest. Attached to it is a *scope*: the exact
                  list of record ids that would answer this question, computed by a sealed pass
                  whose output never reaches the requester. The requester sees counts and
                  document names; they do not see content.

    2. REVIEW     The request appears in the approver's queue. The approver is cleared for the
                  material, so they are shown the actual passages — they are deciding whether to
                  release *these words*, and they get to read them first.

    3. APPROVE    Approval mints an AccessGrant and a one-time key. The grant is bound to five
                  things: the requester, the question, the record set, an expiry, and a use
                  count of one.

    4. ANSWER     The requester re-asks with the key. The guard opens those records and no
                  others. The answer is composed from them, the key is consumed, and the door
                  shuts behind it.

The grant does not promote the requester. Their role is unchanged, every other document stays
shut, and the next question starts from the same place as the last one.

**How the key proves itself.** The key reads ``RGK.<grant_id>.<secret>.<signature>``.
``signature`` is an HMAC-SHA256, under a server secret that never leaves
``data/workbench/security/secret.key``, over the fields that bind the grant — grant id,
requester, record fingerprint, question fingerprint and expiry. Verification is three
independent checks, all of which must pass: the signature has to recompute, the secret has to
match the stored SHA-256 digest (the raw secret is never written to disk), and the stored grant
has to still be live and still be for this requester and this question. Editing any field of a
key invalidates its signature; replaying a spent key fails the use count; handing it to a
colleague fails the requester check; asking a different question with it fails the question
fingerprint.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from workbench.security.records import fingerprint, kind_of, question_fingerprint
from workbench.security.roles import Role, Tag, can_read, hardest, normalise_roles, roles_that_can_read

logger = logging.getLogger(__name__)

#: Record kinds whose plural is not just "+s". The summary is read by the requester and by the
#: approver, so "2 entitys" is not good enough.
_PLURALS = {"entity": "pieces of equipment", "claim": "documented values", "chunk": "passages"}


def _plural(kind: str, n: int) -> str:
    if n != 1:
        return _PLURALS.get(kind, kind + "s")
    singular = {"entity": "piece of equipment", "claim": "documented value", "chunk": "passage"}
    return singular.get(kind, kind)


def _role_of(value) -> Role:
    """A Role from a Role or its name; anything unrecognised is a guest, which decides nothing."""
    if isinstance(value, Role):
        return value
    try:
        return Role(str(value).strip().lower())
    except ValueError:
        return Role.GUEST


KEY_PREFIX = "RGK"
REQUEST_TTL_SECONDS = 24 * 3600          # a request waits a day for a decision
GRANT_TTL_SECONDS = 30 * 60              # an approved key is good for half an hour
MAX_SCOPE_RECORDS = 40                   # a grant is a scalpel; refuse to make it a blanket


class ScopeManifest(BaseModel):
    """What a sealed scoping pass found would be needed to answer one question.

    Record ids only. This object is safe to show the requester: it says *that* three passages in
    the CDU manual bear on their question, never *what* those passages say.
    """
    document_ids: list[str] = Field(default_factory=list)
    record_ids: list[str] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    reader_roles: list[str] = Field(
        default_factory=list,
        description="Roles that can read every document in this scope, and so can decide the "
                    "request. Empty means nobody said, and the tag ladder is used instead.",
    )
    fingerprint: str = ""
    truncated: bool = False

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for rid in self.record_ids:
            out[kind_of(rid)] = out.get(kind_of(rid), 0) + 1
        return dict(sorted(out.items()))

    @property
    def is_empty(self) -> bool:
        return not self.record_ids

    def summary(self) -> str:
        if self.is_empty:
            return "nothing in the restricted documents bears on this question"
        parts = ", ".join(f"{n} {_plural(k, n)}" for k, n in self.counts.items())
        return f"{parts} in {', '.join(self.document_ids)}"

    @classmethod
    def build(cls, records_by_document: dict[str, list[str]], tags: dict[str, Tag],
              readers: dict[str, list] | None = None) -> "ScopeManifest":
        ids: list[str] = []
        docs: list[str] = []
        for doc, rids in sorted(records_by_document.items()):
            if not rids:
                continue
            docs.append(doc)
            ids.extend(rids)
        truncated = len(ids) > MAX_SCOPE_RECORDS
        ids = sorted(set(ids))[:MAX_SCOPE_RECORDS]
        # Only a role that can read *every* document in the scope can release the scope, so the
        # candidate set is the intersection. One request covering two compartments is decidable
        # by whoever is in both, and by nobody if that is nobody.
        cleared: set[Role] | None = None
        for doc in docs:
            here = set(normalise_roles((readers or {}).get(doc) or []))
            if not here:
                cleared = None
                break
            cleared = here if cleared is None else (cleared & here)
        return cls(document_ids=docs, record_ids=ids,
                   tags=sorted({tags.get(d, Tag.SECRET) for d in docs}, key=lambda t: t.value),
                   reader_roles=[r.value for r in normalise_roles(cleared or [])],
                   fingerprint=fingerprint(ids), truncated=truncated)

    def deciders(self) -> list[Role]:
        """Roles allowed to decide a request carrying this scope, lowest first."""
        explicit = normalise_roles(self.reader_roles)
        if explicit:
            return explicit
        return roles_that_can_read(hardest(self.tags) if self.tags else Tag.SECRET)


class AccessRequest(BaseModel):
    request_id: str
    requester: str
    requester_role: Role
    question: str
    question_fp: str
    session_id: str = ""
    scope: ScopeManifest = Field(default_factory=ScopeManifest)
    approver_role: Role = Role.ADMIN
    status: str = "pending"                 # pending | approved | denied | expired | cancelled
    created: float = Field(default_factory=time.time)
    expires: float = 0.0
    decided_by: str | None = None
    decided_at: float | None = None
    note: str = ""
    grant_id: str | None = None

    def live(self, now: float | None = None) -> bool:
        return self.status == "pending" and (now or time.time()) < self.expires

    def describe(self) -> str:
        age = int((time.time() - self.created) / 60)
        return (f"{self.request_id}  {self.status:<9} from {self.requester} ({self.requester_role.value}) "
                f"{age} min ago\n    asked: {self.question}\n    scope: {self.scope.summary()}")


class AccessGrant(BaseModel):
    """An approved, signed, single-use opening over a named set of records."""
    grant_id: str
    request_id: str
    requester: str
    question_fp: str
    record_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    fingerprint: str = ""
    key_digest: str = ""                    # SHA-256 of the secret half of the key
    signature: str = ""                     # HMAC over the binding fields
    issued_by: str = ""
    issued_at: float = Field(default_factory=time.time)
    expires: float = 0.0
    max_uses: int = 1
    uses: int = 0
    revoked: bool = False
    used_at: list[float] = Field(default_factory=list)

    def spent(self) -> bool:
        return self.uses >= self.max_uses

    def live(self, now: float | None = None) -> bool:
        return not self.revoked and not self.spent() and (now or time.time()) < self.expires

    def status(self) -> str:
        if self.revoked:
            return "revoked"
        if self.spent():
            return "used"
        if time.time() >= self.expires:
            return "expired"
        return "live"

    def minutes_left(self) -> int:
        return max(0, int((self.expires - time.time()) / 60))


class EscalationError(RuntimeError):
    """A request or key could not be honoured. The message is safe to show the user."""


class EscalationStore:
    """Requests, grants and the server secret, persisted under ``<security_dir>/``."""

    def __init__(self, security_dir: Path, *, request_ttl: int = REQUEST_TTL_SECONDS,
                 grant_ttl: int = GRANT_TTL_SECONDS) -> None:
        self.dir = Path(security_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.requests_path = self.dir / "access_requests.json"
        self.grants_path = self.dir / "access_grants.json"
        self.secret_path = self.dir / "secret.key"
        self.request_ttl = request_ttl
        self.grant_ttl = grant_ttl
        self._requests: dict[str, AccessRequest] = {}
        self._grants: dict[str, AccessGrant] = {}
        self._secret = self._load_secret()
        self._load()

    # ------------------------------------------------------------------ the server secret
    def _load_secret(self) -> bytes:
        if self.secret_path.exists():
            return self.secret_path.read_bytes()
        secret = secrets.token_bytes(32)
        self.secret_path.write_bytes(secret)
        try:
            self.secret_path.chmod(0o600)
        except OSError:
            pass
        logger.info("generated a new signing secret for access grants")
        return secret

    def _sign(self, grant: AccessGrant) -> str:
        """HMAC over exactly the fields that must not change after approval."""
        body = "|".join([grant.grant_id, grant.request_id, grant.requester, grant.question_fp,
                         grant.fingerprint, f"{grant.expires:.0f}", str(grant.max_uses)])
        return hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        for path, target, model in ((self.requests_path, self._requests, AccessRequest),
                                    (self.grants_path, self._grants, AccessGrant)):
            if not path.exists():
                continue
            try:
                for row in json.loads(path.read_text(encoding="utf-8")).get("items", []):
                    obj = model.model_validate(row)
                    target[obj.request_id if model is AccessRequest else obj.grant_id] = obj
            except Exception as exc:
                logger.error("%s unreadable (%s); starting empty", path.name, exc)
                target.clear()

    def _save_requests(self) -> None:
        self._write(self.requests_path, [r.model_dump(mode="json") for r in self._requests.values()])

    def _save_grants(self) -> None:
        self._write(self.grants_path, [g.model_dump(mode="json") for g in self._grants.values()])

    @staticmethod
    def _write(path: Path, items: list[dict]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({"updated": time.time(), "items": items}, indent=2), encoding="utf-8")
        tmp.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    # ------------------------------------------------------------------ raising a request
    def raise_request(self, *, requester: str, requester_role: Role, question: str, scope: ScopeManifest,
                      session_id: str = "") -> AccessRequest:
        """Create (or return) the pending request for this person and this question.

        Asking the same question twice does not raise a second request — the approver should see
        one item to decide, not a queue filling up with retries.
        """
        if scope.is_empty:
            raise EscalationError("Nothing in the restricted documents bears on that question, so there is nothing to request.")
        qfp = question_fingerprint(question)
        for existing in self._requests.values():
            if (existing.requester == requester and existing.question_fp == qfp and existing.live()):
                return existing
        approver = scope.deciders()
        request = AccessRequest(
            request_id=f"AR-{uuid.uuid4().hex[:8].upper()}",
            requester=requester, requester_role=requester_role, question=question.strip(), question_fp=qfp,
            session_id=session_id, scope=scope,
            approver_role=approver[0] if approver else Role.ADMIN,
            expires=time.time() + self.request_ttl,
        )
        self._requests[request.request_id] = request
        self._save_requests()
        logger.info("access request %s raised by %s for %s", request.request_id, requester, scope.document_ids)
        return request

    def cancel_request(self, request_id: str, by: str) -> AccessRequest:
        request = self._require_request(request_id)
        if request.requester != by:
            raise EscalationError("Only the person who raised a request may cancel it.")
        request.status = "cancelled"
        request.decided_by = by
        request.decided_at = time.time()
        self._save_requests()
        return request

    # ------------------------------------------------------------------ deciding
    def pending_for(self, approver_role: Role | str, *, include_all: bool = False) -> list[AccessRequest]:
        """The queue this approver should look at: requests they are actually cleared to release."""
        now = time.time()
        out = []
        for r in self._requests.values():
            if r.status == "pending" and now >= r.expires:
                r.status = "expired"
            if not include_all and r.status != "pending":
                continue
            if include_all or _role_of(approver_role) in r.scope.deciders():
                out.append(r)
        self._save_requests()
        return sorted(out, key=lambda r: -r.created)

    def approve(self, request_id: str, *, approver: str, approver_role: Role, note: str = "",
                ttl_seconds: int | None = None) -> tuple[AccessRequest, AccessGrant, str]:
        """Mint a grant and its one-time key. Returns (request, grant, raw_key).

        The raw key is returned once and never stored; only its SHA-256 digest is kept.
        """
        request = self._require_request(request_id)
        self._check_decidable(request, approver, approver_role)
        secret = secrets.token_urlsafe(24)
        grant = AccessGrant(
            grant_id=f"G-{uuid.uuid4().hex[:10].upper()}",
            request_id=request.request_id, requester=request.requester, question_fp=request.question_fp,
            record_ids=list(request.scope.record_ids), document_ids=list(request.scope.document_ids),
            fingerprint=request.scope.fingerprint,
            key_digest=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
            issued_by=approver, expires=time.time() + (ttl_seconds or self.grant_ttl),
        )
        grant.signature = self._sign(grant)
        key = f"{KEY_PREFIX}.{grant.grant_id}.{secret}.{grant.signature[:32]}"
        request.status = "approved"
        request.decided_by = approver
        request.decided_at = time.time()
        request.note = note
        request.grant_id = grant.grant_id
        self._grants[grant.grant_id] = grant
        self._save_grants()
        self._save_requests()
        logger.info("access request %s approved by %s -> grant %s", request_id, approver, grant.grant_id)
        return request, grant, key

    def deny(self, request_id: str, *, approver: str, approver_role: Role, note: str = "") -> AccessRequest:
        request = self._require_request(request_id)
        self._check_decidable(request, approver, approver_role)
        request.status = "denied"
        request.decided_by = approver
        request.decided_at = time.time()
        request.note = note
        self._save_requests()
        return request

    def _check_decidable(self, request: AccessRequest, approver: str, approver_role: Role) -> None:
        if request.status != "pending":
            raise EscalationError(f"{request.request_id} is already {request.status}.")
        if time.time() >= request.expires:
            request.status = "expired"
            self._save_requests()
            raise EscalationError(f"{request.request_id} expired without a decision.")
        if request.requester == approver:
            raise EscalationError("A request cannot be approved by the person who raised it.")
        deciders = request.scope.deciders()
        if _role_of(approver_role) not in deciders:
            needed = hardest(request.scope.tags) if request.scope.tags else Tag.SECRET
            who = ", ".join(r.value for r in deciders) or "nobody currently cleared"
            raise EscalationError(
                f"{request.request_id} covers {needed.value} material your role cannot read. "
                f"It has to be decided by: {who}.")

    # ------------------------------------------------------------------ redeeming a key
    def redeem(self, key: str, *, requester: str, question: str) -> AccessGrant:
        """Verify a key and return the grant it names. Raises EscalationError with the reason.

        Every check below is a way a key can be wrong, and each is refused separately so the
        audit trail records which one failed.
        """
        grant = self._parse_and_find(key)
        if grant.revoked:
            raise EscalationError("That access key has been revoked.")
        if grant.spent():
            raise EscalationError(f"That access key has already been used ({grant.uses}/{grant.max_uses}). "
                                  "Raise a new request if you need the material again.")
        if time.time() >= grant.expires:
            raise EscalationError("That access key has expired. Raise a new request.")
        if grant.requester != requester:
            raise EscalationError("That access key was issued to a different person and cannot be used by you.")
        if grant.question_fp != question_fingerprint(question):
            raise EscalationError("That access key was issued for a different question. It opens only the material "
                                 "that was approved for the question it was raised against.")
        if not hmac.compare_digest(grant.signature, self._sign(grant)):
            raise EscalationError("That access key failed its signature check and will not be honoured.")
        if grant.fingerprint != fingerprint(grant.record_ids):
            raise EscalationError("The approved scope no longer matches the grant and will not be honoured.")
        return grant

    def _parse_and_find(self, key: str) -> AccessGrant:
        parts = (key or "").strip().split(".")
        if len(parts) != 4 or parts[0] != KEY_PREFIX:
            raise EscalationError("That does not look like an access key. They read RGK.<grant>.<secret>.<signature>.")
        _, grant_id, secret, sig_head = parts
        grant = self._grants.get(grant_id)
        if grant is None:
            raise EscalationError("No access grant matches that key.")
        if not hmac.compare_digest(hashlib.sha256(secret.encode("utf-8")).hexdigest(), grant.key_digest):
            raise EscalationError("That access key is not valid.")
        if not hmac.compare_digest(sig_head, grant.signature[:32]):
            raise EscalationError("That access key failed its signature check and will not be honoured.")
        return grant

    def consume(self, grant: AccessGrant) -> None:
        """Spend one use. Called only after an answer was actually released from the grant."""
        grant.uses += 1
        grant.used_at.append(time.time())
        self._save_grants()
        logger.info("access grant %s consumed (%d/%d)", grant.grant_id, grant.uses, grant.max_uses)

    def revoke(self, grant_id: str, by: str = "") -> AccessGrant:
        grant = self._grants.get(grant_id)
        if grant is None:
            raise EscalationError(f"No grant {grant_id}.")
        grant.revoked = True
        self._save_grants()
        logger.info("access grant %s revoked by %s", grant_id, by or "system")
        return grant

    # ------------------------------------------------------------------ reading
    def request(self, request_id: str) -> AccessRequest | None:
        return self._requests.get(request_id)

    def _require_request(self, request_id: str) -> AccessRequest:
        request = self._requests.get((request_id or "").strip().upper())
        if request is None:
            raise EscalationError(f"No access request {request_id}.")
        return request

    def requests_of(self, requester: str) -> list[AccessRequest]:
        return sorted([r for r in self._requests.values() if r.requester == requester], key=lambda r: -r.created)

    def grant(self, grant_id: str) -> AccessGrant | None:
        return self._grants.get(grant_id)

    def all_requests(self) -> list[AccessRequest]:
        return sorted(self._requests.values(), key=lambda r: -r.created)

    def all_grants(self) -> list[AccessGrant]:
        return sorted(self._grants.values(), key=lambda g: -g.issued_at)
