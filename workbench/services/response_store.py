"""Released answers, kept whole so they can be turned into deliverables later.

The session file keeps the prose and the security envelope of every turn — enough to redraw a
conversation. Exporting a Word/Excel/PowerPoint file needs the *typed blocks and evidence* as
well, so the full ``FinalResponse`` is written here, one JSON per response, namespaced by the
principal it was released to. Reading one back requires the same principal (or an
administrator), which keeps a response as private as the answer it was.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from workbench.core.result import FinalResponse

logger = logging.getLogger(__name__)


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)[:120] or "x"


class ResponseStore:
    def __init__(self, root: Path, keep: int = 2000) -> None:
        self.dir = Path(root)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.keep = keep

    def path_for(self, response_id: str) -> Path:
        return self.dir / f"{_safe(response_id)}.json"

    def save(self, resp: FinalResponse, *, owner: str, role: str, question: str = "") -> Path:
        path = self.path_for(resp.response_id)
        payload = {"owner": owner, "role": role, "question": question, "saved": time.time(),
                   "response": resp.model_dump(mode="json")}
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception as exc:
            logger.warning("response %s not saved: %s", resp.response_id, exc)
        self._prune()
        return path

    def load(self, response_id: str) -> tuple[FinalResponse, dict] | None:
        path = self.path_for(response_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return FinalResponse.model_validate(raw["response"]), {k: v for k, v in raw.items() if k != "response"}

    def load_for(self, response_id: str, principal) -> FinalResponse:
        """The response, if this principal may see it: its owner, or an administrator."""
        found = self.load(response_id)
        if found is None:
            raise KeyError(f"No released answer {response_id!r} is on record.")
        resp, meta = found
        role = getattr(getattr(principal, "role", None), "value", None) or str(getattr(principal, "role", ""))
        if meta.get("owner") != getattr(principal, "username", None) and role != "admin":
            raise PermissionError("That answer was released to someone else.")
        return resp

    def recent(self, owner: str | None = None, limit: int = 50) -> list[dict]:
        rows = []
        for p in sorted(self.dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if owner and raw.get("owner") != owner:
                continue
            r = raw.get("response", {})
            rows.append({"response_id": r.get("response_id"), "session_id": r.get("session_id"), "owner": raw.get("owner"),
                         "question": raw.get("question", "")[:160], "task_type": r.get("task_type"), "status": r.get("status"),
                         "saved": raw.get("saved"), "evidence": len(r.get("evidence") or []),
                         "requires_human_review": r.get("requires_human_review", False)})
            if len(rows) >= limit:
                break
        return rows

    def _prune(self) -> None:
        files = sorted(self.dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
        for f in files[: max(0, len(files) - self.keep)]:
            try:
                f.unlink()
            except OSError:
                pass
