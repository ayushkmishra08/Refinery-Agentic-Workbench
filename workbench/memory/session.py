"""Session memory: conversation turns, last resolved entities, last task, uploaded documents.

Used by the Context Resolver to resolve "it", "this pump", "the same equipment", and by the
Task Classifier for follow-ups ("and the shutdown?"). Persisted as JSON per session under
data/workbench/sessions/ so a server restart keeps the conversation.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import BaseModel, Field


class Turn(BaseModel):
    ts: float = Field(default_factory=time.time)
    request: str
    task_type: str = ""
    entities: list[dict] = Field(default_factory=list)    # ResolvedEntity dumps
    parameter: str | None = None
    scenario: str | None = None
    response_id: str | None = None
    status: str = ""
    answer_preview: str = ""


class SessionState(BaseModel):
    session_id: str
    created: float = Field(default_factory=time.time)
    turns: list[Turn] = Field(default_factory=list)
    uploaded_documents: list[dict] = Field(default_factory=list)   # {document_id, name, chunks, added}
    pending_clarification: dict | None = None
    notes: list[str] = Field(default_factory=list)

    def last_entities(self, n_turns: int = 3) -> list[dict]:
        out: list[dict] = []
        for t in reversed(self.turns[-n_turns:]):
            for e in t.entities:
                if e.get("entity_uid") and e not in out:
                    out.append(e)
        return out

    def last_task_type(self) -> str | None:
        return self.turns[-1].task_type if self.turns else None

    def last_parameter(self) -> str | None:
        for t in reversed(self.turns):
            if t.parameter:
                return t.parameter
        return None


class SessionStore:
    def __init__(self, sessions_dir: Path, ttl_turns: int = 20) -> None:
        self.dir = sessions_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ttl_turns = ttl_turns
        self._cache: dict[str, SessionState] = {}

    def _path(self, session_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in session_id)[:80] or "default"
        return self.dir / f"{safe}.json"

    def load(self, session_id: str) -> SessionState:
        if session_id in self._cache:
            return self._cache[session_id]
        p = self._path(session_id)
        if p.exists():
            try:
                st = SessionState.model_validate_json(p.read_text(encoding="utf-8"))
            except Exception:
                st = SessionState(session_id=session_id)
        else:
            st = SessionState(session_id=session_id)
        self._cache[session_id] = st
        return st

    def save(self, state: SessionState) -> None:
        if len(state.turns) > self.ttl_turns:
            state.turns = state.turns[-self.ttl_turns:]
        self._cache[state.session_id] = state
        self._path(state.session_id).write_text(state.model_dump_json(indent=1), encoding="utf-8")

    def list_sessions(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                out.append({"session_id": raw.get("session_id"), "turns": len(raw.get("turns", [])), "created": raw.get("created")})
            except Exception:
                continue
        return out
