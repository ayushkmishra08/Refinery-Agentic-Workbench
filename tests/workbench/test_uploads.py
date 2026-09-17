"""A document dropped into a chat belongs to that chat, and to nothing else.

An upload is somebody's working file. Nobody has classified it, nobody has reviewed it, and the
person who attached it did not thereby publish it to their colleagues. So the rules are narrow and
worth pinning down:

* it is answerable **in the conversation it was uploaded into**, whatever the uploader's role —
  it is their own file;
* it is invisible to their *other* conversations, to other people, and to the shared corpus;
* it writes nothing into the knowledge layer;
* moving it into the knowledge layer is a separate, deliberate act, and it takes a manager.

The ingest itself (Docling, minutes per document) is not exercised here; the registry, the
visibility rules and the permission gate are, and those are what broke.
"""
from __future__ import annotations

import pytest

from workbench.core.events import EventBus
from workbench.core.knowledge import DocumentInfo


@pytest.fixture
def events():
    """A bus that swallows what it is given; these tests are about the decision, not the trace."""
    return EventBus()


class FakeUploadBackend:
    """Stands in for the indexed PDF: enough of a backend to be registered and looked up."""

    name = "session_docs"

    def __init__(self, *document_ids: str) -> None:
        self._docs = [DocumentInfo(document_id=d, title=d, total_pages=1) for d in document_ids]

    def documents(self):
        return list(self._docs)


class TestSessionScope:
    def test_an_upload_belongs_to_the_conversation_it_was_uploaded_into(self, secure_orch):
        key = secure_orch.sessions.key_for("user", "chat-1")
        secure_orch.session_uploads[key] = FakeUploadBackend("vendor-datasheet")

        assert secure_orch.upload_document_ids(key) == ["vendor-datasheet"]

    def test_it_is_invisible_to_every_other_conversation_and_person(self, secure_orch):
        mine = secure_orch.sessions.key_for("user", "chat-1")
        secure_orch.session_uploads[mine] = FakeUploadBackend("vendor-datasheet")

        my_other_chat = secure_orch.sessions.key_for("user", "chat-2")
        someone_else = secure_orch.sessions.key_for("manager", "chat-1")
        assert secure_orch.upload_document_ids(my_other_chat) == []
        assert secure_orch.upload_document_ids(someone_else) == []

    def test_it_never_joins_the_shared_corpus(self, secure_orch):
        before = [d.document_id for d in secure_orch.knowledge.documents()]
        key = secure_orch.sessions.key_for("user", "chat-1")
        secure_orch.session_uploads[key] = FakeUploadBackend("vendor-datasheet")

        after = [d.document_id for d in secure_orch.knowledge.documents()]
        assert after == before, "an upload must not appear in the corpus everyone else reads"

    def test_the_uploader_sees_it_alongside_the_corpus(self, secure_orch):
        key = secure_orch.sessions.key_for("user", "chat-1")
        secure_orch.session_uploads[key] = FakeUploadBackend("vendor-datasheet")

        view = secure_orch._knowledge_for(key)
        ids = [d.document_id for d in view.documents()]
        assert "vendor-datasheet" in ids
        assert "API 610 pump standard" in ids, "the corpus is still there too"

    def test_dropping_it_leaves_nothing_behind(self, secure_orch):
        key = secure_orch.sessions.key_for("user", "chat-1")
        secure_orch.session_uploads[key] = FakeUploadBackend("a", "b")

        assert secure_orch.drop_session_uploads(key) == 2
        assert secure_orch.upload_document_ids(key) == []
        assert secure_orch.drop_session_uploads(key) == 0, "dropping twice is not an error"


class TestAskingAboutTheAttachment:
    """"This catalogue" means the file in front of you, not the refinery."""

    ATTACHED = ["vendor-catalogue"]

    @pytest.mark.parametrize("question", [
        "What equipment does this catalogue cover?",
        "What does this document cover?",
        "What is in the attached pdf?",
        "Summarise the uploaded datasheet",
        "Which models are listed in this brochure?",
    ])
    def test_a_question_about_the_attachment_is_recognised(self, secure_orch, question):
        assert secure_orch._asks_about_the_attachment(question, self.ATTACHED)

    @pytest.mark.parametrize("question", [
        "What is the operating pressure of the AVG-100 air cooler?",
        "How do I start up the crude desalter?",
        "What is the normal flow rate of the crude charge pump?",
        "Compare the CPU-5.4 and the CPU-11.8.",
    ])
    def test_an_ordinary_question_still_reaches_the_corpus(self, secure_orch, question):
        assert not secure_orch._asks_about_the_attachment(question, self.ATTACHED)

    def test_with_nothing_attached_it_never_fires(self, secure_orch):
        """Otherwise "what does this manual say" would narrow the corpus to nothing."""
        assert not secure_orch._asks_about_the_attachment("What does this document cover?", [])


class TestSurveyInsteadOfClarifying:
    """Pointing at your own attachment gets it surveyed, never a question back.

    "Tell me about this document" names no equipment, so the resolver finds none and the run heads
    for "which pump did you mean?" — which reads as though the workstation never noticed the file,
    and is unanswerable anyway: the person is asking what is in there *because* they cannot name
    anything in it yet.
    """

    ATTACHED = ["vendor-catalogue"]

    def _resolved(self, text, task_type, entities=()):
        from workbench.core.request import StructuredRequest, TaskType, UserRequest

        return StructuredRequest(original=UserRequest(text=text), task_type=task_type, entities=list(entities))

    @pytest.mark.parametrize("text", [
        "Tell me about the contents of this uploded document",   # the user's own typo, verbatim
        "Tell me about this document",
        "What is this file about?",
        "Describe the attached catalogue",
    ])
    def test_a_vague_question_about_the_attachment_becomes_a_survey(self, secure_orch, text, events):
        from workbench.core.request import TaskType

        req = self._resolved(text, TaskType.LOOKUP)
        out = secure_orch._survey_instead_of_clarifying(req, self.ATTACHED, events)
        assert out.task_type is TaskType.INVENTORY

    def test_an_ambiguous_one_does_too(self, secure_orch, events):
        from workbench.core.request import TaskType

        req = self._resolved("tell me about this pdf", TaskType.AMBIGUOUS)
        assert secure_orch._survey_instead_of_clarifying(req, self.ATTACHED, events).task_type is TaskType.INVENTORY

    def test_it_leaves_a_question_that_names_equipment_alone(self, secure_orch, events):
        """An entity resolved means there is something to answer about; do not hijack it."""
        from workbench.core.request import ResolvedEntity, TaskType

        req = self._resolved("what is the design pressure of the AVG-100 in this document",
                             TaskType.LOOKUP, [ResolvedEntity(mention="AVG-100", entity_uid="e1", name="AVG-100")])
        assert secure_orch._survey_instead_of_clarifying(req, self.ATTACHED, events).task_type is TaskType.LOOKUP

    def test_it_never_fires_without_an_attachment(self, secure_orch, events):
        from workbench.core.request import TaskType

        req = self._resolved("tell me about this document", TaskType.LOOKUP)
        assert secure_orch._survey_instead_of_clarifying(req, [], events).task_type is TaskType.LOOKUP

    def test_it_leaves_a_procedure_question_alone(self, secure_orch, events):
        """Only the task types that would end in a clarification are redirected."""
        from workbench.core.request import TaskType

        req = self._resolved("how do I start up the unit in this manual", TaskType.PROCEDURE)
        assert secure_orch._survey_instead_of_clarifying(req, self.ATTACHED, events).task_type is TaskType.PROCEDURE


class TestPromotion:
    def test_an_engineer_cannot_add_a_document_to_the_knowledge_layer(self, secure_orch, tokens):
        with pytest.raises(PermissionError, match="Manager"):
            secure_orch.promote_upload(tokens["user"], "chat-1", "vendor-datasheet")

    def test_a_manager_may_but_only_for_something_they_actually_uploaded(self, secure_orch, tokens):
        # the permission check passes, so the failure is about the document, not the role
        with pytest.raises(KeyError, match="was not uploaded"):
            secure_orch.promote_upload(tokens["manager"], "chat-1", "never-seen-this")

    def test_signing_in_is_required(self, secure_orch):
        with pytest.raises(PermissionError):
            secure_orch.promote_upload(None, "chat-1", "vendor-datasheet")


class TestArtefacts:
    def test_a_session_upload_writes_nothing_into_the_knowledge_layer(self, secure_cfg, monkeypatch):
        """`persist=False` is what keeps `data/knowledge/` free of unreviewed documents."""
        from workbench.services import ingest

        saved: list = []
        monkeypatch.setattr(ingest, "save_chunks", lambda *a, **kw: saved.append(a), raising=False)
        scratch = ingest._scratch_pipeline(secure_cfg, _tiny_pdf(secure_cfg))

        assert scratch.paths.knowledge_dir != secure_cfg.knowledge_layer.paths.knowledge_dir
        assert scratch.paths.parsed_dir != secure_cfg.knowledge_layer.paths.parsed_dir
        assert "uploads" in str(scratch.paths.parsed_dir), "a chat attachment does not land in data/parsed"

    def test_the_same_file_reuses_its_parse_and_two_different_files_do_not_collide(self, secure_cfg):
        from workbench.services import ingest

        one = _tiny_pdf(secure_cfg, b"%PDF-1.4 one")
        again = _tiny_pdf(secure_cfg, b"%PDF-1.4 one", name="renamed.pdf")
        other = _tiny_pdf(secure_cfg, b"%PDF-1.4 two", name="other.pdf")

        assert ingest._scratch_pipeline(secure_cfg, one).paths.parsed_dir == \
            ingest._scratch_pipeline(secure_cfg, again).paths.parsed_dir, "same bytes, same cache"
        assert ingest._scratch_pipeline(secure_cfg, one).paths.parsed_dir != \
            ingest._scratch_pipeline(secure_cfg, other).paths.parsed_dir, "different bytes, different cache"


def _tiny_pdf(cfg, body: bytes = b"%PDF-1.4 sample", name: str = "sample.pdf"):
    path = cfg.paths.uploads_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path
