"""Append-only audit trail: one JSONL file per session under data/workbench/audit/.

Every run writes: request, structured request, plan, per-step results (summary, evidence keys,
llm calls, duration), safety flags, governance decision, resource events. Nothing is ever
rewritten; the audit id is returned in the FinalResponse. Each session file is hash-chained
(``workbench.sovereignty.hashchain``) so a past entry cannot be altered without detection.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from workbench.sovereignty.hashchain import ChainVerification, HashChainedLog


class AuditStore:
    def __init__(self, audit_dir: Path) -> None:
        self.dir = audit_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._chains: dict[str, HashChainedLog] = {}
        self._lock = threading.Lock()

    def new_id(self) -> str:
        return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    def _chain(self, session_id: str) -> HashChainedLog:
        name = _safe(session_id)
        with self._lock:
            chain = self._chains.get(name)
            if chain is None:
                chain = self._chains[name] = HashChainedLog(self.dir / f"{name}.jsonl", name=name)
            return chain

    def write(self, session_id: str, audit_id: str, kind: str, payload: dict[str, Any]) -> None:
        self._chain(session_id).append({"audit_id": audit_id, "kind": kind, **payload})

    def read(self, session_id: str, audit_id: str | None = None) -> list[dict]:
        rows = self._chain(session_id).read()
        return [r for r in rows if audit_id is None or r.get("audit_id") == audit_id]

    def verify(self, session_id: str) -> ChainVerification:
        return self._chain(session_id).verify()

    def verify_all(self) -> list[ChainVerification]:
        return [HashChainedLog(p).verify() for p in sorted(self.dir.glob("*.jsonl")) if p.name != "hitl.jsonl"]


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)[:80] or "default"
