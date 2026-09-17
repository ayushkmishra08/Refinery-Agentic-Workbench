"""The security log: an append-only record of who asked for what, and what the system decided.

Separate from the run audit (`data/workbench/audit/<session>.jsonl`) on purpose. That one
answers "how was this answer produced"; this one answers "who touched classified material, and
under whose authority" — and it is the file a reviewer reads after an incident, so it should not
be buried in retrieval traces.

Every event carries the principal, their role, the outcome, and enough identifiers to follow the
thread: a request id, a grant id, the documents involved. Nothing it writes is itself sensitive —
record ids are opaque and no document text is ever logged — so the log can be read by a reviewer
who is not cleared for the material the events concern.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

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
}


class SecurityAudit:
    """Append-only JSONL under ``data/workbench/security/security.jsonl``."""

    def __init__(self, security_dir: Path) -> None:
        self.dir = Path(security_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "security.jsonl"

    def write(self, event: str, *, principal: str = "guest", role: str = "guest", outcome: str = "",
              **fields: Any) -> dict:
        record = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event,
                  "principal": principal, "role": role, "outcome": outcome, **fields}
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        except Exception as exc:                    # logging must never break a request
            logger.warning("security audit write failed: %s", exc)
        if event in ("access_denied", "key_rejected", "release_blocked", "lockout", "login_failed"):
            logger.info("security: %s by %s (%s) — %s", event, principal, role, outcome)
        return record

    def read(self, *, event: str | None = None, principal: str | None = None, limit: int = 200) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event and row.get("event") != event:
                continue
            if principal and row.get("principal") != principal:
                continue
            rows.append(row)
        return rows[-limit:]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.read(limit=100_000):
            out[row.get("event", "?")] = out.get(row.get("event", "?"), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))
