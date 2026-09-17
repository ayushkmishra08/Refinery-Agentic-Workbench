"""Conversations are kept, listed per person, and picked up again — attachments included.

The web client used to hold the whole conversation in the tab: reload and it was gone, and every
question a person ever asked went into one server-side session nobody could see. Now the server's
record is the conversation — each exchange is written as it lands, with the released answer and
the security envelope it was released with — and the client only remembers *which* one is open.

Three things are pinned down here:

* listing is by **owner**, read from inside the file, and hands back the id the client sent;
* a stored turn carries enough to redraw the exchange — the full answer and its envelope;
* a conversation's attachments come back after the process that indexed them has gone.
"""
from __future__ import annotations

import pytest

from workbench.core.knowledge import DocumentInfo
from workbench.memory.session import SessionState, SessionStore, Turn


class TestListing:
    def test_a_person_sees_their_own_conversations_and_nobody_elses(self, tmp_path):
        store = SessionStore(tmp_path / "sessions")
        a = store.load(store.key_for("alice", "c-1"), owner="alice", owner_role="user")
        a.turns.append(Turn(request="What is the desalter pressure?", status="answered"))
        store.save(a)
        b = store.load(store.key_for("bob", "c-9"), owner="bob", owner_role="manager")
        b.turns.append(Turn(request="Start-up of the CDU", status="answered"))
        store.save(b)

        mine = store.list_sessions(owner="alice")
        assert [c["session_id"] for c in mine] == ["c-1"], "the public id, not the namespaced file key"
        assert mine[0]["title"].startswith("What is the desalter")
        assert mine[0]["turns"] == 1
        assert store.list_sessions(owner="bob")[0]["session_id"] == "c-9"
        assert store.list_sessions(owner="carol") == []

    def test_the_filter_is_the_recorded_owner_not_the_filename(self, tmp_path):
        """A file named like someone else's must not show up in their list."""
        store = SessionStore(tmp_path / "sessions")
        forged = SessionState(session_id=store.key_for("alice", "c-2"), owner="mallory", owner_role="user")
        forged.turns.append(Turn(request="something", status="answered"))
        store.save(forged)

        assert store.list_sessions(owner="alice") == [], "the filename says alice; the record says otherwise, and the record wins"
        assert len(store.list_sessions(owner="mallory")) == 1, "it is listed for the owner the record names"

    def test_newest_first_and_attachments_are_named(self, tmp_path):
        store = SessionStore(tmp_path / "sessions")
        old = store.load(store.key_for("alice", "old"), owner="alice")
        old.turns.append(Turn(ts=100.0, request="first"))
        store.save(old)
        new = store.load(store.key_for("alice", "new"), owner="alice")
        new.turns.append(Turn(ts=200.0, request="second"))
        new.uploaded_documents.append({"document_id": "GNH-eng", "name": "GNH-eng.pdf", "kind": "pdf", "added": 150.0})
        store.save(new)

        rows = store.list_sessions(owner="alice")
        assert [r["session_id"] for r in rows] == ["new", "old"]
        assert rows[0]["attachments"] == ["GNH-eng.pdf"]

    def test_delete_removes_the_file_and_the_cache(self, tmp_path):
        store = SessionStore(tmp_path / "sessions")
        key = store.key_for("alice", "gone")
        st = store.load(key, owner="alice")
        st.turns.append(Turn(request="bye"))
        store.save(st)

        assert store.delete(key) is True
        assert store.list_sessions(owner="alice") == []
        assert store.load(key, owner="alice").turns == [], "a fresh, empty conversation — not the cached old one"
        assert store.delete(key) is False, "deleting twice is not an error"


class TestWhatATurnKeeps:
    def test_a_turn_can_be_redrawn_from_what_is_stored(self, tmp_path):
        """The preview is for the next turn; the full answer and envelope are for the person coming back."""
        store = SessionStore(tmp_path / "sessions")
        st = store.load(store.key_for("alice", "c"), owner="alice")
        st.turns.append(Turn(
            request="Q", answer_preview="short", answer_markdown="## The full answer\n\nwith **markup** [1]",
            security={"classification": "INTERNAL", "source_documents": ["API 610 pump standard"]},
        ))
        store.save(st)

        back = SessionStore(tmp_path / "sessions").load(store.key_for("alice", "c"), owner="alice")
        assert back.turns[0].answer_markdown.startswith("## The full answer")
        assert back.turns[0].security["classification"] == "INTERNAL"


class TestOrchestratorSurface:
    def test_listing_goes_through_the_token_and_a_guest_gets_nothing(self, secure_orch, tokens):
        key = secure_orch.sessions.key_for("user", "c-77")
        st = secure_orch.sessions.load(key, owner="user", owner_role="user")
        st.turns.append(Turn(request="hello", status="answered"))
        secure_orch.sessions.save(st)

        assert [c["session_id"] for c in secure_orch.list_sessions(tokens["user"])] == ["c-77"]
        assert secure_orch.list_sessions(tokens["manager"]) == [], "someone else's conversations are not listed"
        assert secure_orch.list_sessions(None) == []

    def test_deleting_a_conversation_also_drops_its_attachments(self, secure_orch, tokens):
        key = secure_orch.sessions.key_for("user", "c-78")
        st = secure_orch.sessions.load(key, owner="user", owner_role="user")
        st.turns.append(Turn(request="hello"))
        secure_orch.sessions.save(st)

        class Fake:
            def documents(self):
                return [DocumentInfo(document_id="att", title="att", total_pages=1)]

        secure_orch.session_uploads[key] = Fake()
        assert secure_orch.delete_session(tokens["user"], "c-78") is True
        assert secure_orch.upload_document_ids(key) == []

    def test_an_attachment_is_rebuilt_from_the_session_record_after_a_restart(self, secure_orch, monkeypatch, tmp_path):
        """The index lives in memory on purpose; the record of *what* was attached does not."""
        from workbench.orchestration import orchestrator as mod

        pdf = tmp_path / "sheet.pdf"
        pdf.write_bytes(b"%PDF-1.4 stub")
        key = secure_orch.sessions.key_for("user", "c-79")
        st = secure_orch.sessions.load(key, owner="user", owner_role="user")
        st.uploaded_documents.append({"document_id": "sheet", "name": "sheet.pdf", "kind": "pdf",
                                      "added": 1.0, "path": str(pdf)})
        secure_orch.sessions.save(st)

        built = []

        class FakeIndex:
            info = DocumentInfo(document_id="sheet", title="sheet", total_pages=1)

        class FakeBackend:
            def __init__(self, indexes, cfg):
                self.indexes = {i.info.document_id: i for i in indexes}

            def documents(self):
                return [i.info for i in self.indexes.values()]

        import workbench.services.ingest as ingest

        monkeypatch.setattr(ingest, "build_index_from_pdf", lambda cfg, path, *a, **k: (built.append(path), FakeIndex())[1])
        monkeypatch.setattr(ingest, "SessionDocumentsBackend", FakeBackend)

        assert secure_orch.upload_document_ids(key) == [], "nothing in memory: the process restarted"
        secure_orch._ensure_uploads(key, st)
        assert secure_orch.upload_document_ids(key) == ["sheet"]
        assert built == [pdf], "rebuilt from the recorded path"

        secure_orch._ensure_uploads(key, st)
        assert built == [pdf], "and not rebuilt again while it is already in memory"
        del mod  # silence the unused-import warning; the module is imported for its side effects

    def test_a_missing_file_is_skipped_rather_than_fatal(self, secure_orch, tmp_path):
        key = secure_orch.sessions.key_for("user", "c-80")
        st = secure_orch.sessions.load(key, owner="user", owner_role="user")
        st.uploaded_documents.append({"document_id": "lost", "name": "lost.pdf", "kind": "pdf",
                                      "added": 1.0, "path": str(tmp_path / "not-there.pdf")})
        secure_orch._ensure_uploads(key, st)
        assert secure_orch.upload_document_ids(key) == []


@pytest.fixture
def _unused():
    return None
