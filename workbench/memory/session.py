"""Session memory: conversation turns, last resolved entities, last task, uploaded documents.

Used by the Context Resolver to resolve "it", "this pump", "the same equipment", and by the
Task Classifier for follow-ups ("and the shutdown?"). Persisted as JSON per session under
data/workbench/sessions/ so a server restart keeps the conversation.

**Sessions belong to a principal.** A conversation holds the previous answers, so two people
sharing a session id would share whatever the more-cleared of them was told. The orchestrator
therefore namespaces every session by the signed-in username (``alice__web``), and a state whose
``owner`` does not match the caller is not returned. Picking someone else's session id gets you
your own empty conversation, not theirs.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import BaseModel, Field


class Turn(BaseModel):
    ts: float = Field(default_factory=time.time)
    request: str                                          # what the engineer typed
    rewritten_request: str = ""                           # what it was read as, when it was a follow-up
    task_type: str = ""
    entities: list[dict] = Field(default_factory=list)    # ResolvedEntity dumps
    parameter: str | None = None
    scenario: str | None = None
    response_id: str | None = None
    status: str = ""
    followup_kind: str = "new"
    corrections: list[dict] = Field(default_factory=list)  # EntityCorrection dumps: tags that were not documented
    answer_preview: str = Field(default="", description="The composed answer, long enough that the next turn can refer back to it")
    # Everything a screen needs to redraw this exchange later. The preview above is for the *next
    # turn's* benefit; these are for the person coming back tomorrow to continue where they left
    # off. The envelope is kept so the classification banner is redrawn as it was released, not
    # recomputed against whatever the caller may read now.
    answer_markdown: str = Field(default="", description="The full released answer, for redrawing the conversation")
    security: dict = Field(default_factory=dict, description="The security envelope the answer was released with")


class SessionState(BaseModel):
    session_id: str
    owner: str = Field(default="", description="The principal this conversation belongs to; sessions are never shared across roles")
    owner_role: str = ""
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

    @staticmethod
    def key_for(owner: str, session_id: str) -> str:
        """The stored name of one person's conversation. Two roles never collide."""
        return f"{owner or 'anonymous'}__{session_id}"

    def load(self, session_id: str, *, owner: str = "", owner_role: str = "") -> SessionState:
        """The conversation for this session id, belonging to this principal.

        ``owner`` is checked, not trusted from the file: a state stored under someone else's name
        is discarded and a fresh one returned, so a guessed session id yields nothing.
        """
        if session_id in self._cache:
            cached = self._cache[session_id]
            if not owner or cached.owner == owner:
                return cached
        p = self._path(session_id)
        st = None
        if p.exists():
            try:
                st = SessionState.model_validate_json(p.read_text(encoding="utf-8"))
            except Exception:
                st = None
        if st is None or (owner and st.owner and st.owner != owner):
            st = SessionState(session_id=session_id, owner=owner, owner_role=owner_role)
        st.owner = st.owner or owner
        st.owner_role = owner_role or st.owner_role
        self._cache[session_id] = st
        return st

    def save(self, state: SessionState) -> None:
        if len(state.turns) > self.ttl_turns:
            state.turns = state.turns[-self.ttl_turns:]
        self._cache[state.session_id] = state
        self._path(state.session_id).write_text(state.model_dump_json(indent=1), encoding="utf-8")

    def list_sessions(self, owner: str | None = None) -> list[dict]:
        """The conversations on disk — one person's when ``owner`` is given, newest first.

        Files are named by the namespaced key (``alice__web-1a2b``), and the state inside carries
        the owner, so the filter is on the recorded owner and not on a filename prefix somebody
        could imitate. The public ``session_id`` handed back is the un-namespaced one the client
        sent, because that is the only id the client knows.
        """
        out = []
        for p in self.dir.glob("*.json"):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if owner is not None and raw.get("owner") != owner:
                continue
            turns = raw.get("turns", [])
            key = str(raw.get("session_id") or "")
            prefix = f"{raw.get('owner') or 'anonymous'}__"
            public = key[len(prefix):] if key.startswith(prefix) else key
            first = next((t.get("request") for t in turns if t.get("request")), "") or ""
            out.append({
                "session_id": public,
                "title": " ".join(first.split())[:90] or "New conversation",
                "turns": len(turns),
                "created": raw.get("created"),
                "updated": (turns[-1].get("ts") if turns else raw.get("created")),
                "attachments": [d.get("name") or d.get("document_id")
                                for d in raw.get("uploaded_documents", []) if d.get("kind") == "pdf"],
                "last_status": (turns[-1].get("status") if turns else ""),
            })
        out.sort(key=lambda r: -(r["updated"] or 0))
        return out

    def delete(self, session_id: str) -> bool:
        """Remove one conversation from disk and from the cache. Returns whether anything existed."""
        self._cache.pop(session_id, None)
        p = self._path(session_id)
        if p.exists():
            p.unlink()
            return True
        return False
