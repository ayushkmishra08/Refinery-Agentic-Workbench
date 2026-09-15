"""Human-in-the-loop registry: responses flagged for review are recorded so a reviewer can
approve / reject them later (data/workbench/audit/hitl.jsonl). The API exposes them under
/reviews. Nothing is blocked automatically — the answer is delivered with the flag — because
the workbench advises; it never actuates."""
from __future__ import annotations

import json
import time
from pathlib import Path

from workbench.core.result import FinalResponse


class HITLRegistry:
    def __init__(self, audit_dir: Path) -> None:
        self.path = audit_dir / "hitl.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, resp: FinalResponse, request_text: str) -> None:
        if not resp.requires_human_review:
            return
        rec = {"ts": time.time(), "response_id": resp.response_id, "session_id": resp.session_id, "audit_id": resp.audit_trail_id,
               "reason": resp.review_reason, "request": request_text, "status": "pending", "task_type": resp.task_type.value}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def pending(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows = [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]
        decided = {r["response_id"] for r in rows if r.get("status") in ("approved", "rejected")}
        return [r for r in rows if r.get("status") == "pending" and r["response_id"] not in decided]

    def decide(self, response_id: str, decision: str, reviewer: str, note: str = "") -> dict:
        rec = {"ts": time.time(), "response_id": response_id, "status": decision, "reviewer": reviewer, "note": note}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
