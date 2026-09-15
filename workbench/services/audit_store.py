"""Append-only audit trail: one JSONL file per session under data/workbench/audit/.

Every run writes: request, structured request, plan, per-step results (summary, evidence keys,
llm calls, duration), safety flags, governance decision, resource events. Nothing is ever
rewritten; the audit id is returned in the FinalResponse.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


class AuditStore:
    def __init__(self, audit_dir: Path) -> None:
        self.dir = audit_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    def write(self, session_id: str, audit_id: str, kind: str, payload: dict[str, Any]) -> None:
        rec = {"ts": time.time(), "audit_id": audit_id, "kind": kind, **payload}
        path = self.dir / f"{_safe(session_id)}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def read(self, session_id: str, audit_id: str | None = None) -> list[dict]:
        path = self.dir / f"{_safe(session_id)}.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r for r in rows if audit_id is None or r.get("audit_id") == audit_id]


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)[:80] or "default"
