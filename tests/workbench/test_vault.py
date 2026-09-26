"""Envelope encryption: seal per branch, unwrap per role, rotate, revoke, wipe."""
from __future__ import annotations

import json

import pytest

from workbench.security.roles import Role
from workbench.security.vault import (
    EncryptedBranchStore, LocalKMS, SessionKeyring, SessionKeyringRegistry, VaultAccessError, VaultIntegrityError,
    load_sealed_index, seal_index_cache, unwrap, vault_status,
)


@pytest.fixture
def kms(tmp_path):
    return LocalKMS(tmp_path / "security")


@pytest.fixture
def store(tmp_path, kms):
    return EncryptedBranchStore(tmp_path / "vault", kms)


def test_envelope_round_trip_by_role(store):
    info = store.seal("CDU", b"secret index", ["manager", "admin"])
    assert info.roles == ["manager", "admin"]
    assert store.open("CDU", "admin") == b"secret index"
    assert store.open("CDU", "manager") == b"secret index"
    with pytest.raises(VaultAccessError):
        store.open("CDU", "user")
    assert store.is_sealed("CDU") and store.sealed_branches() == ["CDU"]


def test_tampered_ciphertext_is_rejected(store, tmp_path):
    info = store.seal("Doc", b"x" * 500, ["admin"])
    p = tmp_path / "vault" / "Doc" / "index.enc"
    raw = bytearray(p.read_bytes())
    raw[40] ^= 0xFF
    p.write_bytes(bytes(raw))
    with pytest.raises(VaultIntegrityError):
        store.open("Doc", "admin")


def test_rotate_role_key_invalidates_old_material(kms, store):
    store.seal("Doc", b"payload", ["manager", "admin"])
    old_version = kms.role_key_version("manager")
    old_blob = dict(kms._branches["Doc"]["manager"])
    old_rwk = kms._role_key("manager", old_version)
    new = kms.rotate_role_key("manager")
    assert new.version == old_version + 1
    assert store.open("Doc", "manager") == b"payload"        # still opens under the new key
    with pytest.raises(VaultAccessError):
        kms._role_key("manager", old_version)                 # the retired version is gone
    # the old wrapped blob was re-wrapped; unwrapping the old blob under the new key fails
    new_rwk = kms._role_key("manager", new.version)
    with pytest.raises(VaultIntegrityError):
        unwrap(new_rwk, old_blob, f"ck:Doc:manager:v{old_version}")
    assert unwrap(old_rwk, old_blob, f"ck:Doc:manager:v{old_version}")  # only the dead key could
    assert kms.status()["role_keys"]["manager"]["version"] == new.version


def test_revoke_role(kms, store):
    store.seal("Doc", b"payload", ["manager", "admin"])
    kms.revoke_role("Doc", "manager")
    with pytest.raises(VaultAccessError):
        store.open("Doc", "manager")
    assert store.open("Doc", "admin") == b"payload"
    assert kms.wrapped_roles("Doc") == ["admin"]


def test_session_keyring_wipe(kms, store):
    store.seal("Doc", b"payload", ["admin"])
    ring = SessionKeyring(kms, "admin", "alice", store)
    assert ring.open_branch("Doc") == b"payload"
    assert ring.holds() == ["Doc"]
    buf = ring._keys["Doc"]
    assert any(buf)
    assert ring.wipe() == 1
    assert ring.holds() == [] and not any(buf)
    with pytest.raises(VaultAccessError):
        SessionKeyring(kms, "user", "bob", store).open_branch("Doc")
    reg = SessionKeyringRegistry(kms, store)
    r = reg.get_or_create("tok", "admin", "alice")
    r.open_branch("Doc")
    assert reg.status()["keyrings"] == 1 and reg.status()["sessions"][0]["branches"] == ["Doc"]
    assert reg.wipe("tok") and reg.status()["keyrings"] == 0


def test_key_events_chain_verifies(kms, store):
    store.seal("A", b"a", ["admin"])
    kms.rotate_role_key("admin")
    v = kms.key_events.verify()
    assert v.ok and v.entries >= 4
    events = [e["event"] for e in kms.key_events.read()]
    assert "branch_sealed" in events and "role_key_rotated" in events


class _Cfg:
    def __init__(self, root):
        class P:
            pass
        self.paths = P()
        self.paths.cache_dir = root / "cache"
        self.paths.security_dir = root / "security"


class _Classifications:
    def readers_of(self, doc_id):
        return [Role.ADMIN] if doc_id == "CDU" else [Role.USER, Role.MANAGER, Role.ADMIN]


def test_seal_index_cache_and_load(tmp_path):
    cfg = _Cfg(tmp_path)
    for doc, payload in (("CDU", {"doc": "cdu", "n": 1}), ("API610", {"doc": "api", "n": 2})):
        d = cfg.paths.cache_dir / doc
        d.mkdir(parents=True)
        (d / "index.json").write_text(json.dumps(payload), encoding="utf-8")
    kms = LocalKMS(cfg.paths.security_dir)
    store = EncryptedBranchStore(tmp_path / "vault", kms)
    sealed = seal_index_cache(cfg, kms, store, _Classifications(), shred=True)
    assert sorted(s.branch for s in sealed) == ["API610", "CDU"]
    assert not (cfg.paths.cache_dir / "CDU" / "index.json").exists()
    assert load_sealed_index(cfg, store, "CDU", "admin") == {"doc": "cdu", "n": 1}
    assert load_sealed_index(cfg, store, "API610", "user") == {"doc": "api", "n": 2}
    with pytest.raises(VaultAccessError):
        load_sealed_index(cfg, store, "CDU", "user")
    st = vault_status(cfg, kms, store)
    assert st["plaintext_indexes_on_disk"] == [] and len(st["sealed_branches"]) == 2
    assert st["kms"]["branches"]["CDU"]["roles"] == ["admin"]


def test_master_key_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RWB_MASTER_KEY_HEX", "ab" * 32)
    kms = LocalKMS(tmp_path / "s")
    assert kms.master_source == "env" and not kms.master_path.exists()
    monkeypatch.setenv("RWB_MASTER_KEY_HEX", "zz")
    with pytest.raises(VaultIntegrityError):
        LocalKMS(tmp_path / "s2")
