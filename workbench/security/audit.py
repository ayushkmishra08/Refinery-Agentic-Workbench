"""The security log: an append-only record of who asked for what, and what the system decided.

Separate from the run audit (`data/workbench/audit/<session>.jsonl`) on purpose. That one
answers "how was this answer produced"; this one answers "who touched classified material, and
under whose authority" — and it is the file a reviewer reads after an incident, so it should not
be buried in retrieval traces.

Every event carries the principal, their role, the outcome, and enough identifiers to follow the
thread: a request id, a grant id, the documents involved. Nothing it writes is itself sensitive —
record ids are opaque and no document text is ever logged — so the log can be read by a reviewer
who is not cleared for the material the events concern.

The file is **hash-chained** (``workbench.sovereignty.hashchain``): every line carries the hash
of the line before it, so an entry that is edited, dropped or reordered after the fact is
detectable by ``verify()`` — the tamper-evidence a ledger gives, without a ledger.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from workbench.sovereignty.hashchain import ChainVerification, HashChainedLog

logger = logging.getLogger(__name__)

# Events, and what each one means when you are reading the log back
EVENTS = {
    "login": "someone signed in",
    "login_failed": "a password was refused",
    "lockout": "an account was locked after repeated failures",
    "logout": "a token was revoked",
    "access_allowed": "a request ran against documents the role may read",
    "access_partial": "a request ran, with one or more documents withheld",
    "access_denied": "a request was refused: nothing readable",
    "request_raised": "an access request was raised to a higher role",
    "request_approved": "an access request was approved and a key issued",
    "request_denied": "an access request was refused by the approver",
    "request_cancelled": "a requester withdrew their own request",
    "key_redeemed": "an access key was verified and its records opened",
    "key_rejected": "an access key was presented and refused",
    "grant_consumed": "an approved grant was spent on an answer",
    "grant_revoked": "a grant was revoked before it was spent",
    "release_blocked": "a finished answer failed the release check and was withheld",
    "classification_changed": "a document's tag was reassigned",
    "conversation_viewed": "a supervisor read a lower rank's conversation",
    "key_rotated": "a role's wrapping key was rotated; old sessions can no longer unwrap",
    "branch_sealed": "a knowledge branch was envelope-encrypted into the vault",
    "branch_opened": "a session unwrapped a branch's content key",
    "sandbox_run": "code ran in the sandbox",
    "draft_signed_off": "a reviewer signed off a draft after resolving every flag",
    "signoff_blocked": "a sign-off was refused because flags were still open",
    "model_package_verified": "a signed model package passed checksum and signature checks",
    "model_package_rejected": "a model package failed verification and was not loaded",
    "egress_blocked": "an outbound connection to a non-local address was refused in-process",
}


class SecurityAudit:
    """Append-only, hash-chained JSONL under ``data/workbench/security/security.jsonl``."""

    def __init__(self, security_dir: Path) -> None:
        self.dir = Path(security_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "security.jsonl"
        self.chain = HashChainedLog(self.path, name="security")

    def write(self, event: str, *, principal: str = "guest", role: str = "guest", outcome: str = "",
              **fields: Any) -> dict:
        record = {"iso": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event,
                  "principal": principal, "role": role, "outcome": outcome, **fields}
        try:
            record = self.chain.append(record)
        except Exception as exc:                    # logging must never break a request
            logger.warning("security audit write failed: %s", exc)
        if event in ("access_denied", "key_rejected", "release_blocked", "lockout", "login_failed"):
            logger.info("security: %s by %s (%s) — %s", event, principal, role, outcome)
        return record

    def read(self, *, event: str | None = None, principal: str | None = None, limit: int = 200) -> list[dict]:
        rows = self.chain.read()
        if event:
            rows = [r for r in rows if r.get("event") == event]
        if principal:
            rows = [r for r in rows if r.get("principal") == principal]
        return rows[-limit:]

    def verify(self) -> ChainVerification:
        """Walk the whole chain and say whether any past entry was altered."""
        return self.chain.verify()

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.read(limit=100_000):
            out[row.get("event", "?")] = out.get(row.get("event", "?"), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))
