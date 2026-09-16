"""Role-based access control: credentials, tokens, classification, the guard and the gate.

These are the only tests that switch the access gate on (``secure_cfg`` / ``secure_orch`` in
conftest). Everything here asks the same question from two sides: can an uncleared caller
reach the CDU material by any route, and does a cleared one get a normal answer.
"""
from __future__ import annotations

import time

import pytest

from workbench.core.knowledge import DocumentInfo
from workbench.core.request import UserRequest
from workbench.security.auth import (
    LOCKOUT_SECONDS,
    MAX_FAILED_ATTEMPTS,
    AuthError,
    AuthService,
    hash_password,
)
from workbench.security.classification import ClassificationRegistry
from workbench.security.guard import GuardedKnowledgeService
from workbench.security.policy import AccessPolicy
from workbench.security.roles import Clearance, Role, clears, role_clearance


# --------------------------------------------------------------------------- passwords
def test_a_password_is_stored_only_as_a_salted_digest(tmp_path):
    auth = AuthService(tmp_path, seed_default=False)
    auth.add_user("lead", "1234", Role.LEAD_ENGINEER)
    raw = (tmp_path / "users.json").read_text(encoding="utf-8")
    assert "1234" not in raw
    assert '"digest"' in raw and '"salt"' in raw


def test_the_same_password_hashes_differently_for_two_accounts(tmp_path):
    auth = AuthService(tmp_path, seed_default=False)
    a = auth.add_user("one", "same-password", Role.ENGINEER)
    b = auth.add_user("two", "same-password", Role.ENGINEER)
    assert a.salt != b.salt and a.digest != b.digest


def test_hash_password_is_deterministic_for_a_given_salt():
    salt, digest, iterations = hash_password("1234")
    again = hash_password("1234", bytes.fromhex(salt), iterations)
    assert again[1] == digest
    assert hash_password("1235", bytes.fromhex(salt), iterations)[1] != digest


def test_the_default_account_is_the_lead_engineer_with_the_seeded_password(tmp_path, monkeypatch):
    monkeypatch.delenv("RWB_LEAD_PASSWORD", raising=False)
    auth = AuthService(tmp_path)
    principal = auth.authenticate("lead", "1234")
    assert principal.authenticated and principal.role is Role.LEAD_ENGINEER
    assert principal.must_change_password, "a seeded password must announce itself"


def test_an_environment_password_replaces_the_seeded_one(tmp_path, monkeypatch):
    monkeypatch.setenv("RWB_LEAD_PASSWORD", "a-real-password")
    auth = AuthService(tmp_path)
    with pytest.raises(AuthError):
        auth.authenticate("lead", "1234")
    assert auth.authenticate("lead", "a-real-password").authenticated


# --------------------------------------------------------------------------- login refusals
def test_a_wrong_password_is_refused_without_saying_which_half_was_wrong(tmp_path):
    auth = AuthService(tmp_path, seed_default=False)
    auth.add_user("lead", "1234", Role.LEAD_ENGINEER)
    with pytest.raises(AuthError) as wrong_password:
        auth.authenticate("lead", "9999")
    with pytest.raises(AuthError) as wrong_user:
        auth.authenticate("nobody", "1234")
    assert "not correct" in str(wrong_password.value) and "not correct" in str(wrong_user.value)
    assert "no such user" not in str(wrong_user.value).lower()


def test_repeated_failures_lock_the_account(tmp_path):
    auth = AuthService(tmp_path, seed_default=False)
    auth.add_user("lead", "1234", Role.LEAD_ENGINEER)
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(AuthError):
            auth.authenticate("lead", "wrong")
    with pytest.raises(AuthError) as locked:
        auth.authenticate("lead", "wrong")
    assert "locked" in str(locked.value)
    assert locked.value.locked_until and locked.value.locked_until > time.time()
    # the right password does not open a locked account either
    with pytest.raises(AuthError) as still_locked:
        auth.authenticate("lead", "1234")
    assert "locked" in str(still_locked.value)


def test_a_successful_login_clears_the_failure_count(tmp_path):
    auth = AuthService(tmp_path, seed_default=False)
    auth.add_user("lead", "1234", Role.LEAD_ENGINEER)
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(AuthError):
            auth.authenticate("lead", "wrong")
    auth.authenticate("lead", "1234")
    with pytest.raises(AuthError) as exc:
        auth.authenticate("lead", "wrong")
    assert f"{MAX_FAILED_ATTEMPTS - 1} attempt" in str(exc.value)


# --------------------------------------------------------------------------- tokens
def test_a_token_names_its_principal_and_survives_a_restart(tmp_path):
    auth = AuthService(tmp_path, seed_default=False)
    auth.add_user("lead", "1234", Role.LEAD_ENGINEER)
    token = auth.authenticate("lead", "1234").token
    assert auth.principal_for(token).role is Role.LEAD_ENGINEER
    reopened = AuthService(tmp_path, seed_default=False)
    assert reopened.principal_for(token).role is Role.LEAD_ENGINEER


def test_the_raw_token_is_never_written_to_disk(tmp_path):
    auth = AuthService(tmp_path, seed_default=False)
    auth.add_user("lead", "1234", Role.LEAD_ENGINEER)
    token = auth.authenticate("lead", "1234").token
    assert token not in (tmp_path / "tokens.json").read_text(encoding="utf-8")


def test_an_unknown_expired_or_revoked_token_is_a_guest(tmp_path):
    auth = AuthService(tmp_path, token_ttl_seconds=-1, seed_default=False)
    auth.add_user("lead", "1234", Role.LEAD_ENGINEER)
    expired = auth.authenticate("lead", "1234").token
    assert auth.principal_for(expired).role is Role.GUEST
    assert auth.principal_for(None).role is Role.GUEST
    assert auth.principal_for("made-up").role is Role.GUEST

    live = AuthService(tmp_path, seed_default=False)
    live.add_user("lead2", "1234", Role.LEAD_ENGINEER)
    token = live.authenticate("lead2", "1234").token
    assert live.revoke(token) is True
    assert live.principal_for(token).role is Role.GUEST


def test_changing_a_role_invalidates_tokens_minted_under_the_old_one(tmp_path):
    auth = AuthService(tmp_path, seed_default=False)
    auth.add_user("person", "1234", Role.LEAD_ENGINEER)
    token = auth.authenticate("person", "1234").token
    auth.add_user("person", "1234", Role.OPERATOR)          # demoted
    assert auth.principal_for(token).role is Role.GUEST


# --------------------------------------------------------------------------- roles
def test_the_clearance_ladder_places_the_lead_engineer_above_the_engineer():
    assert role_clearance(Role.LEAD_ENGINEER) is Clearance.CONFIDENTIAL
    assert role_clearance(Role.ENGINEER) is Clearance.INTERNAL
    assert clears(Role.LEAD_ENGINEER, Clearance.CONFIDENTIAL)
    assert not clears(Role.ENGINEER, Clearance.CONFIDENTIAL)
    assert not clears(Role.GUEST, Clearance.INTERNAL)
    assert clears(Role.ADMIN, Clearance.SECRET)


def test_an_unknown_role_or_classification_fails_closed():
    assert role_clearance("shift-mascot") is Clearance.PUBLIC
    assert not clears(Role.ENGINEER, "something-new")


# --------------------------------------------------------------------------- classification
def _doc(document_id="CDU operating manual", **kw):
    return DocumentInfo(document_id=document_id, title=kw.pop("title", "Operating Manual"),
                        document_type=kw.pop("document_type", "operating_manual"), unit=kw.pop("unit", "CDU-II"), **kw)


def test_the_cdu_manual_is_confidential(tmp_path):
    reg = ClassificationRegistry(tmp_path / "classifications.json")
    assert reg.classify(_doc()).clearance is Clearance.CONFIDENTIAL


def test_an_unrecognised_document_is_classified_conservatively(tmp_path):
    reg = ClassificationRegistry(tmp_path / "classifications.json")
    dc = reg.classify(_doc("mystery", title="Untitled", document_type="", unit=None))
    assert dc.clearance is Clearance.CONFIDENTIAL and "conservatively" in dc.reason


def test_a_session_upload_is_internal_not_confidential(tmp_path):
    reg = ClassificationRegistry(tmp_path / "classifications.json")
    dc = reg.classify(_doc("upload-1", title="scanned note", document_type="", unit=None, origin="upload"))
    assert dc.clearance is Clearance.INTERNAL


def test_classifications_persist_and_a_pinned_entry_is_not_overwritten(tmp_path):
    path = tmp_path / "classifications.json"
    reg = ClassificationRegistry(path)
    reg.classify(_doc())
    reg.set("CDU operating manual", Clearance.SECRET, "escalated by the unit head")
    assert ClassificationRegistry(path).classify(_doc()).clearance is Clearance.SECRET


def test_a_document_id_never_seen_is_not_treated_as_public(tmp_path):
    reg = ClassificationRegistry(tmp_path / "classifications.json")
    assert reg.clearance_of("never-classified") is Clearance.CONFIDENTIAL


# --------------------------------------------------------------------------- policy
def test_the_policy_splits_documents_by_clearance(tmp_path):
    reg = ClassificationRegistry(tmp_path / "c.json")
    policy = AccessPolicy(reg)
    auth = AuthService(tmp_path, seed_default=False)
    auth.add_user("lead", "1234", Role.LEAD_ENGINEER)
    auth.add_user("eng", "1234", Role.ENGINEER)
    docs = [_doc(), _doc("note", title="Safety awareness notice", document_type="notice", unit=None)]

    lead = policy.decide(auth.authenticate("lead", "1234"), docs)
    assert lead.allowed == ["CDU operating manual", "note"] and lead.fully_allowed

    eng = policy.decide(auth.authenticate("eng", "1234"), docs)
    assert eng.allowed == ["note"] and eng.denied == ["CDU operating manual"]
    assert "lead_engineer" in eng.required_roles()
    assert "Operating Manual" in eng.message() and "confidential" in eng.message()


def test_a_disabled_policy_allows_everything(tmp_path):
    policy = AccessPolicy(ClassificationRegistry(tmp_path / "c.json"), enabled=False)
    from workbench.security.auth import GUEST

    assert policy.decide(GUEST, [_doc()]).fully_allowed


# --------------------------------------------------------------------------- the guard
def test_the_guard_hides_every_record_from_a_document_that_is_not_allowed(mock_knowledge):
    blocked = GuardedKnowledgeService(mock_knowledge, allowed_document_ids=[])
    assert blocked.documents() == []
    assert blocked.list_entities(limit=50) == []
    assert blocked.search_claims(limit=50) == []
    assert blocked.search_chunks("crude charge pump", k=5) == []
    assert blocked.procedures(query="start up", limit=5) == []
    assert blocked.resolve_entity("11-PM-01") == []
    assert blocked.withheld_count() > 0


def test_the_guard_is_transparent_when_the_document_is_allowed(mock_knowledge):
    allowed = GuardedKnowledgeService(mock_knowledge, allowed_document_ids=["CDU operating manual"])
    assert [d.document_id for d in allowed.documents()] == ["CDU operating manual"]
    assert allowed.resolve_entity("11-PM-01")
    assert allowed.search_claims(limit=5)
    assert allowed.withheld_count() == 0


def test_the_guard_cannot_be_walked_around_through_a_single_record_lookup(mock_knowledge):
    entity = mock_knowledge.list_entities(limit=1)[0]
    blocked = GuardedKnowledgeService(mock_knowledge, allowed_document_ids=[])
    assert blocked.get_entity(entity.entity_uid) is None
    procs = mock_knowledge.procedures(limit=1)
    if procs:
        assert blocked.get_procedure(procs[0].procedure_id) is None


def test_entity_counts_follow_the_same_filter(mock_knowledge):
    assert GuardedKnowledgeService(mock_knowledge, allowed_document_ids=[]).entity_type_counts() == {}
    assert GuardedKnowledgeService(mock_knowledge, allowed_document_ids=["CDU operating manual"]).entity_type_counts()


# --------------------------------------------------------------------------- the gate, end to end
def test_an_unauthenticated_request_is_refused_and_told_how_to_sign_in(secure_orch):
    resp = secure_orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="sec-1"))
    assert resp.status == "unauthorized"
    assert not resp.evidence, "no evidence may leave the building before a sign-in"
    assert "Lead Engineer" in resp.answer_markdown and "login" in resp.answer_markdown
    assert "482" not in resp.answer_markdown, "no documented value may leak through the refusal"


def test_an_engineer_without_the_clearance_is_refused_too(secure_orch):
    secure_orch.auth.add_user("eng", "1234", "engineer")
    token = secure_orch.login("eng", "1234").token
    resp = secure_orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?",
                                       session_id="sec-2", auth_token=token))
    assert resp.status == "unauthorized" and "Lead Engineer" in resp.answer_markdown


def test_the_lead_engineer_gets_the_answer(secure_orch):
    secure_orch.auth.add_user("lead", "1234", "lead_engineer", display_name="Lead Engineer")
    token = secure_orch.login("lead", "1234").token
    resp = secure_orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?",
                                       session_id="sec-3", auth_token=token))
    assert resp.status == "answered" and resp.evidence


def test_a_revoked_token_stops_working_immediately(secure_orch):
    secure_orch.auth.add_user("lead2", "1234", "lead_engineer")
    token = secure_orch.login("lead2", "1234").token
    assert secure_orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?",
                                       session_id="sec-4", auth_token=token)).status == "answered"
    secure_orch.logout(token)
    assert secure_orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?",
                                       session_id="sec-4", auth_token=token)).status == "unauthorized"


def test_every_access_decision_is_written_to_the_audit_trail(secure_orch):
    resp = secure_orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="sec-5"))
    access = [e for e in secure_orch.audit.read("sec-5", resp.audit_trail_id) if e["kind"] == "access"]
    assert len(access) == 1, "an access decision must be recorded whether it allowed or denied"
    assert access[0]["role"] == "guest" and access[0]["authenticated"] is False
    assert access[0]["denied"] == ["CDU operating manual"] and access[0]["allowed"] == []


def test_the_role_a_caller_claims_in_the_body_is_ignored(secure_orch):
    """user_role is a frontend hint; only the token decides what may be read."""
    resp = secure_orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?",
                                       session_id="sec-6", user_role="lead_engineer"))
    assert resp.status == "unauthorized"


# --------------------------------------------------------------------------- the HTTP surface
@pytest.fixture
def secure_client(secure_orch):
    from fastapi.testclient import TestClient

    from workbench.app import api

    secure_orch.auth.add_user("lead", "1234", Role.LEAD_ENGINEER, display_name="Lead Engineer")
    previous = api._orch
    api._orch = secure_orch
    with TestClient(api.app) as c:
        yield c
    api._orch = previous


def test_the_api_refuses_an_unauthenticated_ask_with_401(secure_client):
    r = secure_client.post("/ask", json={"text": "What is the normal flow rate of the crude charge pump?", "session_id": "api-sec"})
    assert r.status_code == 401
    assert "482" not in r.text


def test_login_returns_a_token_that_opens_the_documents(secure_client):
    login = secure_client.post("/auth/login", json={"username": "lead", "password": "1234"})
    assert login.status_code == 200
    body = login.json()
    assert body["role"] == "lead_engineer" and body["readable_documents"] == ["CDU operating manual"]

    headers = {"Authorization": f"Bearer {body['token']}"}
    answered = secure_client.post("/ask", headers=headers,
                                  json={"text": "What is the normal flow rate of the crude charge pump?", "session_id": "api-sec2"})
    assert answered.status_code == 200 and answered.json()["status"] == "answered"

    who = secure_client.get("/auth/whoami", headers=headers).json()
    assert who["authenticated"] is True and who["withheld_documents"] == []


def test_a_wrong_password_over_http_is_401_and_a_locked_account_is_423(secure_client):
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        assert secure_client.post("/auth/login", json={"username": "lead", "password": "no"}).status_code == 401
    assert secure_client.post("/auth/login", json={"username": "lead", "password": "no"}).status_code == 423


def test_logging_out_over_http_closes_the_token(secure_client):
    token = secure_client.post("/auth/login", json={"username": "lead", "password": "1234"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert secure_client.post("/auth/logout", headers=headers).json()["revoked"] is True
    assert secure_client.post("/ask", headers=headers,
                              json={"text": "What is the normal flow rate of the crude charge pump?", "session_id": "api-sec3"}).status_code == 401
