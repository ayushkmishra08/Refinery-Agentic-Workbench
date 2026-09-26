"""Hash-chained logs: append, verify, detect tampering, migrate legacy files."""
from __future__ import annotations

import json

from workbench.security.audit import SecurityAudit
from workbench.services.audit_store import AuditStore
from workbench.sovereignty.hashchain import GENESIS, HashChainedLog, entry_hash


def test_append_and_verify(tmp_path):
    log = HashChainedLog(tmp_path / "log.jsonl")
    a = log.append({"event": "one", "n": 1})
    b = log.append({"event": "two", "n": 2})
    assert a["seq"] == 0 and a["prev_hash"] == GENESIS
    assert b["seq"] == 1 and b["prev_hash"] == a["hash"]
    assert entry_hash(0, a["ts"], GENESIS, {"event": "one", "n": 1}) == a["hash"]
    v = log.verify()
    assert v.ok and v.entries == 2 and v.head == b["hash"]
    assert len(log) == 2 and log.head == b["hash"]


def test_edit_is_detected(tmp_path):
    path = tmp_path / "log.jsonl"
    log = HashChainedLog(path)
    for i in range(5):
        log.append({"event": "e", "i": i})
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[2])
    row["i"] = 99
    lines[2] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = HashChainedLog(path).verify()
    assert not v.ok and v.first_bad_seq == 2 and "altered" in v.detail


def test_deletion_and_reorder_are_detected(tmp_path):
    path = tmp_path / "log.jsonl"
    log = HashChainedLog(path)
    for i in range(4):
        log.append({"i": i})
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")
    v = HashChainedLog(path).verify()
    assert not v.ok and v.first_bad_seq == 1
    path.write_text("\n".join([lines[0], lines[2], lines[1], lines[3]]) + "\n", encoding="utf-8")
    assert not HashChainedLog(path).verify().ok


def test_reopen_continues_chain(tmp_path):
    path = tmp_path / "log.jsonl"
    HashChainedLog(path).append({"a": 1})
    second = HashChainedLog(path)
    rec = second.append({"a": 2})
    assert rec["seq"] == 1
    assert second.verify().ok


def test_legacy_file_is_migrated_once(tmp_path):
    path = tmp_path / "legacy.jsonl"
    path.write_text('{"ts": 1.0, "event": "old1"}\n{"ts": 2.0, "event": "old2"}\n', encoding="utf-8")
    log = HashChainedLog(path)
    rec = log.append({"event": "new"})
    rows = log.read()
    assert [r["event"] for r in rows] == ["old1", "old2", "chain_migrated", "new"]
    assert rows[2]["legacy_entries"] == 2
    assert rec["seq"] == 3
    assert log.verify().ok
    assert HashChainedLog(path).verify().ok       # a second open does not migrate again


def test_security_audit_is_chained(tmp_path):
    audit = SecurityAudit(tmp_path)
    audit.write("login", principal="x", role="admin", outcome="ok")
    audit.write("logout", principal="x", role="admin")
    assert audit.verify().ok and audit.verify().entries == 2
    assert [r["event"] for r in audit.read()] == ["login", "logout"]
    assert audit.read(event="login")[0]["principal"] == "x"


def test_run_audit_store_is_chained(tmp_path):
    store = AuditStore(tmp_path / "audit")
    store.write("s1", "a1", "request", {"text": "hi"})
    store.write("s1", "a1", "final", {"status": "answered"})
    store.write("s2", "a2", "request", {"text": "yo"})
    assert len(store.read("s1", "a1")) == 2
    assert store.verify("s1").ok and store.verify("s2").ok
    assert all(v.ok for v in store.verify_all())
