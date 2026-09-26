"""Supervisors read the conversations of ranks strictly below their own — and every read is logged.

Strictly below is the whole safety argument: an answer released to an engineer was drawn only from
documents an engineer may read, and a manager's clearance contains that set. So the logs cannot
put anything above the reader's line in front of them, and no second filter is needed. The other
half of the rule is that peers and superiors are never visible: an administrator does not read
another administrator's conversations here, and nobody reads upward.
"""
from __future__ import annotations

import pytest

from workbench.memory.session import Turn


def _conversation(orch, owner: str, role: str, sid: str, question: str):
    key = orch.sessions.key_for(owner, sid)
    st = orch.sessions.load(key, owner=owner, owner_role=role)
    st.turns.append(Turn(request=question, answer_markdown="an answer", status="answered",
                         security={"classification": "INTERNAL"}))
    orch.sessions.save(st)


@pytest.fixture
def populated(secure_orch):
    _conversation(secure_orch, "user", "user", "u-1", "engineer's question")
    _conversation(secure_orch, "manager", "manager", "m-1", "manager's question")
    _conversation(secure_orch, "admin", "admin", "a-1", "admin's question")
    return secure_orch


class TestWhoSeesWhom:
    def test_a_manager_sees_engineers_only(self, populated, tokens):
        owners = {r["owner"] for r in populated.supervised_sessions(tokens["manager"])}
        assert owners == {"user"}

    def test_an_administrator_sees_managers_and_engineers_but_not_other_administrators(self, populated, tokens):
        populated.auth.add_user("admin2", "admin2-pw", populated.auth._users["admin"].role, display_name="Second admin")
        _conversation(populated, "admin2", "admin", "a-2", "another admin's question")
        owners = {r["owner"] for r in populated.supervised_sessions(tokens["admin"])}
        assert owners == {"user", "manager"}, "peers are not supervised"

    def test_an_engineer_is_refused_outright(self, populated, tokens):
        with pytest.raises(PermissionError):
            populated.supervised_sessions(tokens["user"])

    def test_a_guest_is_refused(self, populated):
        with pytest.raises(PermissionError):
            populated.supervised_sessions(None)

    def test_rows_carry_owner_and_rank(self, populated, tokens):
        row = populated.supervised_sessions(tokens["manager"])[0]
        assert row["owner"] == "user" and row["owner_role"] == "user"
        assert row["title"].startswith("engineer's question")


class TestReadingOne:
    def test_a_manager_reads_an_engineers_conversation_in_full(self, populated, tokens):
        snap = populated.supervised_session(tokens["manager"], "user", "u-1")
        assert snap["turns"][0]["answer_markdown"] == "an answer"
        assert snap["turns"][0]["security"]["classification"] == "INTERNAL"

    def test_reading_upward_is_refused(self, populated, tokens):
        with pytest.raises(PermissionError):
            populated.supervised_session(tokens["manager"], "admin", "a-1")

    def test_reading_a_peer_is_refused(self, populated, tokens):
        populated.auth.add_user("manager2", "m2-pw", populated.auth._users["manager"].role, display_name="Second manager")
        _conversation(populated, "manager2", "manager", "m-2", "peer's question")
        with pytest.raises(PermissionError):
            populated.supervised_session(tokens["manager"], "manager2", "m-2")

    def test_a_missing_conversation_is_not_found_rather_than_created(self, populated, tokens):
        with pytest.raises(KeyError):
            populated.supervised_session(tokens["admin"], "user", "never-existed")

    def test_every_read_is_written_to_the_security_audit(self, populated, tokens):
        populated.supervised_session(tokens["manager"], "user", "u-1")
        blob = populated.security_audit.path.read_text(encoding="utf-8")
        assert "conversation_viewed" in blob
        assert '"owner": "user"' in blob or "owner=user" in blob or '"owner":"user"' in blob
