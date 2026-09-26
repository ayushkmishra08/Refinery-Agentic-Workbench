"""Envelope encryption per knowledge branch, with a local key-management service.

Three layers of key, each wrapping the one below it:

    master key (MK)      one 32-byte key for this server; ``kms/master.key`` or RWB_MASTER_KEY_HEX
      └─ role wrapping keys (RWK)   one per role (user / manager / admin), versioned,
         stored wrapped by the master key in ``kms/keyring.json``
           └─ branch content keys (CK)   one per branch (document), never stored raw:
              stored in ``kms/branches.json`` wrapped once *per role allowed to read it*

Why envelope and not flat encryption: a branch's data is encrypted **once** with its content
key. Who can read it is decided by which roles hold a wrapped copy of that content key. Rotating
a role's wrapping key re-wraps a few dozen 32-byte content keys, not the gigabytes of data under
them; revoking a role from a branch deletes one wrapped copy. Both are instant and neither
touches the ciphertext.

Where the master key lives. By default ``<security_dir>/kms/master.key`` (created on first use,
mode 600). Set ``RWB_MASTER_KEY_HEX`` (64 hex characters) to supply it from the environment
instead — the file is then never written, so the key can live on a removable token that is only
plugged in while the server starts. Everything else on disk (keyring, branches, vault) is
useless without it.

Session scoping. A ``SessionKeyring`` unwraps content keys for one principal's role and keeps
them only as ``bytearray`` objects in this process's memory; ``wipe()`` zeroes them. Nothing in
this module ever writes plaintext to disk. ``shred_plaintext`` overwrites and deletes a
plaintext file once its sealed copy exists.

All cryptography is AES-256-GCM (``cryptography`` package) with random 96-bit nonces and
associated data naming what is being wrapped, so a wrapped blob cannot be swapped between roles,
versions or branches without failing authentication.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, Field

from workbench.sovereignty.hashchain import HashChainedLog

logger = logging.getLogger(__name__)

ROLES = ("user", "manager", "admin")
KEY_BYTES = 32
NONCE_BYTES = 12
MAGIC = b"RKV1"
_MASTER_ENV = "RWB_MASTER_KEY_HEX"


class VaultAccessError(PermissionError):
    """The role has no wrapped copy of this branch's content key: it structurally cannot open it."""


class VaultIntegrityError(RuntimeError):
    """Ciphertext or a wrapped key failed authentication: altered, or the wrong key."""


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)[:80] or "branch"


def _chmod600(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    _chmod600(path)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VaultIntegrityError(f"{path.name} is not valid JSON: {exc}") from exc


def wrap(key: bytes, plaintext: bytes, aad: str) -> dict[str, str]:
    """AES-256-GCM encrypt ``plaintext`` under ``key``; returns {wrapped, nonce} (base64)."""
    nonce = secrets.token_bytes(NONCE_BYTES)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad.encode("utf-8"))
    return {"wrapped": _b64(ct), "nonce": _b64(nonce)}


def unwrap(key: bytes, blob: dict[str, str], aad: str) -> bytes:
    """Inverse of ``wrap``. Raises VaultIntegrityError when the tag does not verify."""
    try:
        return AESGCM(key).decrypt(_unb64(blob["nonce"]), _unb64(blob["wrapped"]), aad.encode("utf-8"))
    except (InvalidTag, KeyError, ValueError) as exc:
        raise VaultIntegrityError(f"wrapped key failed authentication ({aad})") from exc


# --------------------------------------------------------------------------- models
class RoleKeyVersion(BaseModel):
    role: str
    version: int
    created: float
    status: str = "active"          # active | retired


class SealedBranchInfo(BaseModel):
    branch: str
    path: str
    roles: list[str]
    sha256_plain: str
    bytes_plain: int
    bytes_cipher: int
    sealed_at: float
    ck_fingerprint: str


# --------------------------------------------------------------------------- KMS
class LocalKMS:
    """Issues, stores, rotates and revokes keys — on this machine, for this machine."""

    def __init__(self, security_dir: Path) -> None:
        self.dir = Path(security_dir) / "kms"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.master_path = self.dir / "master.key"
        self.keyring_path = self.dir / "keyring.json"
        self.branches_path = self.dir / "branches.json"
        self.key_events = HashChainedLog(self.dir / "key_events.jsonl", name="key_events")
        self._lock = threading.RLock()
        self.master_source = "env" if os.getenv(_MASTER_ENV) else "file"
        self._master = self._load_master()
        self._keyring: dict[str, Any] = _read_json(self.keyring_path)
        self._branches: dict[str, Any] = _read_json(self.branches_path)
        for role in ROLES:
            if role not in self._keyring.get("roles", {}):
                self._create_role_key(role)

    # ---------------------------------------------------------------- master key
    def _load_master(self) -> bytes:
        env = os.getenv(_MASTER_ENV)
        if env:
            try:
                key = bytes.fromhex(env.strip())
            except ValueError as exc:
                raise VaultIntegrityError(f"{_MASTER_ENV} is not hex") from exc
            if len(key) != KEY_BYTES:
                raise VaultIntegrityError(f"{_MASTER_ENV} must be {KEY_BYTES * 2} hex characters")
            return key
        if self.master_path.exists():
            key = self.master_path.read_bytes()
            if len(key) != KEY_BYTES:
                raise VaultIntegrityError("master.key has the wrong length")
            return key
        key = secrets.token_bytes(KEY_BYTES)
        self.master_path.write_bytes(key)
        _chmod600(self.master_path)
        self.key_events.append({"event": "master_created", "source": "file"})
        logger.info("generated a new vault master key at %s", self.master_path)
        return key

    # ---------------------------------------------------------------- role keys
    def _save(self) -> None:
        _write_json(self.keyring_path, self._keyring)
        _write_json(self.branches_path, self._branches)

    @staticmethod
    def _rwk_aad(role: str, version: int) -> str:
        return f"rwk:{role}:v{version}"

    def _create_role_key(self, role: str) -> RoleKeyVersion:
        roles = self._keyring.setdefault("roles", {})
        entry = roles.setdefault(role, {"current": 0, "versions": {}})
        version = int(entry["current"]) + 1
        raw = secrets.token_bytes(KEY_BYTES)
        blob = wrap(self._master, raw, self._rwk_aad(role, version))
        entry["versions"][str(version)] = {**blob, "created": time.time(), "status": "active"}
        entry["current"] = version
        self._save()
        self.key_events.append({"event": "role_key_created", "role": role, "version": version})
        return RoleKeyVersion(role=role, version=version, created=entry["versions"][str(version)]["created"])

    def role_key_version(self, role: str) -> int:
        return int(self._keyring["roles"][role]["current"])

    def _role_key(self, role: str, version: int | None = None) -> bytes:
        entry = self._keyring.get("roles", {}).get(role)
        if entry is None:
            raise VaultAccessError(f"no wrapping key for role {role!r}")
        version = int(entry["current"]) if version is None else int(version)
        blob = entry["versions"].get(str(version))
        if blob is None:
            raise VaultAccessError(f"wrapping key for role {role!r} version {version} no longer exists")
        return unwrap(self._master, blob, self._rwk_aad(role, version))

    def rotate_role_key(self, role: str) -> RoleKeyVersion:
        """Issue a new wrapping key for ``role`` and re-wrap every branch key it holds under it.

        The old version is marked retired for the duration of the re-wrap and then deleted, so a
        session that unwrapped the old RWK — or a copy of the old keyring file — can open nothing.
        The branch ciphertext is untouched.
        """
        with self._lock:
            entry = self._keyring["roles"][role]
            old_version = int(entry["current"])
            entry["versions"][str(old_version)]["status"] = "retired"
            old_key = self._role_key(role, old_version)
            new = self._create_role_key(role)
            new_key = self._role_key(role, new.version)
            rewrapped = 0
            for branch, holders in self._branches.items():
                blob = holders.get(role)
                if blob is None:
                    continue
                ck = unwrap(old_key, blob, self._ck_aad(branch, role, int(blob["rwk_version"])))
                holders[role] = {**wrap(new_key, ck, self._ck_aad(branch, role, new.version)), "rwk_version": new.version}
                rewrapped += 1
            del entry["versions"][str(old_version)]
            self._keyring.setdefault("last_rotation", {})[role] = time.time()
            self._save()
            self.key_events.append({"event": "role_key_rotated", "role": role, "from": old_version,
                                    "to": new.version, "branches_rewrapped": rewrapped})
            logger.info("rotated wrapping key for %s: v%d -> v%d, %d branch key(s) re-wrapped", role, old_version, new.version, rewrapped)
            return new

    # ---------------------------------------------------------------- branch keys
    @staticmethod
    def _ck_aad(branch: str, role: str, rwk_version: int) -> str:
        return f"ck:{branch}:{role}:v{rwk_version}"

    def ensure_branch_key(self, branch: str, roles: list[str]) -> None:
        """Make sure ``branch`` has a content key wrapped for exactly ``roles``.

        A new branch gets a fresh key. An existing branch keeps its key: roles added get a wrapped
        copy, roles dropped lose theirs. The content key itself is only ever in memory here.
        """
        wanted = [r for r in roles if r in ROLES]
        with self._lock:
            holders = self._branches.get(branch)
            if holders:
                # recover the CK through any role that currently holds it
                any_role = next(iter(holders))
                ck = self._unwrap_with(branch, any_role, holders[any_role])
                created = False
            else:
                ck = secrets.token_bytes(KEY_BYTES)
                holders = {}
                created = True
            new_holders: dict[str, Any] = {}
            for role in wanted:
                existing = holders.get(role)
                if existing is not None and int(existing["rwk_version"]) == self.role_key_version(role):
                    new_holders[role] = existing
                    continue
                version = self.role_key_version(role)
                new_holders[role] = {**wrap(self._role_key(role, version), ck, self._ck_aad(branch, role, version)),
                                     "rwk_version": version}
            removed = sorted(set(holders) - set(new_holders))
            self._branches[branch] = new_holders
            self._save()
            self.key_events.append({"event": "branch_key_created" if created else "branch_key_wrapped",
                                    "branch": branch, "roles": wanted, "removed": removed})

    def _unwrap_with(self, branch: str, role: str, blob: dict) -> bytes:
        version = int(blob["rwk_version"])
        return unwrap(self._role_key(role, version), blob, self._ck_aad(branch, role, version))

    def wrapped_roles(self, branch: str) -> list[str]:
        return sorted(self._branches.get(branch, {}), key=ROLES.index)

    def unwrap_branch_key(self, branch: str, role: str) -> bytes:
        """The content key of ``branch`` for ``role`` — or VaultAccessError if that role holds none."""
        with self._lock:
            holders = self._branches.get(branch)
            if not holders:
                raise VaultAccessError(f"branch {branch!r} has no content key")
            blob = holders.get(role)
            if blob is None:
                raise VaultAccessError(f"role {role!r} holds no key for branch {branch!r}")
            return self._unwrap_with(branch, role, blob)

    def revoke_role(self, branch: str, role: str) -> None:
        """Remove ``role``'s wrapped copy of the branch key and rotate that role's wrapping key.

        The rotation is what makes the revocation immediate: a session that already unwrapped the
        role key cannot use it to unwrap anything afterwards, because nothing is wrapped under it.
        """
        with self._lock:
            holders = self._branches.get(branch, {})
            if role in holders:
                del holders[role]
                self._branches[branch] = holders
                self._save()
            self.key_events.append({"event": "branch_role_revoked", "branch": branch, "role": role})
            self.rotate_role_key(role)

    def branches(self) -> list[str]:
        return sorted(self._branches)

    def status(self) -> dict:
        roles = self._keyring.get("roles", {})
        return {
            "master_key_source": self.master_source,
            "master_key_path": None if self.master_source == "env" else str(self.master_path),
            "algorithm": "AES-256-GCM envelope (master -> role wrapping key -> branch content key)",
            "role_keys": {r: {"version": int(v["current"]), "versions_kept": len(v["versions"])} for r, v in roles.items()},
            "last_rotation": self._keyring.get("last_rotation", {}),
            "branches": {b: {"roles": self.wrapped_roles(b),
                             "rwk_versions": {r: int(h["rwk_version"]) for r, h in holders.items()}}
                         for b, holders in sorted(self._branches.items())},
            "branch_count": len(self._branches),
            "wrapped_key_count": sum(len(h) for h in self._branches.values()),
            "key_events": len(self.key_events),
            "key_events_chain": self.key_events.verify().model_dump(),
        }


# --------------------------------------------------------------------------- the vault
class EncryptedBranchStore:
    """Sealed branches on disk: ``<vault_dir>/<branch>/index.enc`` + ``manifest.json``."""

    def __init__(self, vault_dir: Path, kms: LocalKMS) -> None:
        self.dir = Path(vault_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.kms = kms

    def _branch_dir(self, branch: str) -> Path:
        return self.dir / _safe(branch)

    def seal(self, branch: str, plaintext: bytes, roles: list[str]) -> SealedBranchInfo:
        self.kms.ensure_branch_key(branch, roles)
        ck = self.kms.unwrap_branch_key(branch, self.kms.wrapped_roles(branch)[0])
        nonce = secrets.token_bytes(NONCE_BYTES)
        ct = AESGCM(ck).encrypt(nonce, plaintext, branch.encode("utf-8"))
        bdir = self._branch_dir(branch)
        bdir.mkdir(parents=True, exist_ok=True)
        enc = bdir / "index.enc"
        enc.write_bytes(MAGIC + nonce + ct)
        _chmod600(enc)
        info = SealedBranchInfo(
            branch=branch, path=str(enc), roles=self.kms.wrapped_roles(branch),
            sha256_plain=hashlib.sha256(plaintext).hexdigest(), bytes_plain=len(plaintext),
            bytes_cipher=len(ct), sealed_at=time.time(),
            ck_fingerprint=hashlib.sha256(ck).hexdigest()[:16],
        )
        _write_json(bdir / "manifest.json", info.model_dump())
        self.kms.key_events.append({"event": "branch_sealed", "branch": branch, "roles": info.roles,
                                    "bytes": info.bytes_plain, "sha256": info.sha256_plain})
        return info

    def open(self, branch: str, role: str) -> bytes:
        """Decrypt a branch into memory for ``role``. Never touches disk with plaintext."""
        ck = self.kms.unwrap_branch_key(branch, role)
        enc = self._branch_dir(branch) / "index.enc"
        if not enc.exists():
            raise FileNotFoundError(f"branch {branch!r} is not sealed")
        raw = enc.read_bytes()
        if raw[: len(MAGIC)] != MAGIC:
            raise VaultIntegrityError(f"{enc} is not a vault file")
        nonce = raw[len(MAGIC): len(MAGIC) + NONCE_BYTES]
        ct = raw[len(MAGIC) + NONCE_BYTES:]
        try:
            return AESGCM(ck).decrypt(nonce, ct, branch.encode("utf-8"))
        except InvalidTag as exc:
            raise VaultIntegrityError(f"sealed branch {branch!r} failed authentication: altered or wrong key") from exc

    unseal_to_memory = open

    def manifest(self, branch: str) -> dict:
        return _read_json(self._branch_dir(branch) / "manifest.json")

    def is_sealed(self, branch: str) -> bool:
        return (self._branch_dir(branch) / "index.enc").exists()

    def sealed_branches(self) -> list[str]:
        out = []
        for d in sorted(self.dir.iterdir()):
            if d.is_dir() and (d / "index.enc").exists():
                m = _read_json(d / "manifest.json")
                out.append(m.get("branch") or d.name)
        return out

    @staticmethod
    def shred_plaintext(path: Path, passes: int = 2) -> bool:
        """Overwrite a plaintext file with random bytes and delete it."""
        path = Path(path)
        if not path.exists():
            return False
        size = path.stat().st_size
        try:
            with path.open("r+b") as fh:
                for _ in range(passes):
                    fh.seek(0)
                    remaining = size
                    while remaining > 0:
                        chunk = min(remaining, 1 << 20)
                        fh.write(secrets.token_bytes(chunk))
                        remaining -= chunk
                    fh.flush()
                    os.fsync(fh.fileno())
        finally:
            path.unlink(missing_ok=True)
        return True


# --------------------------------------------------------------------------- sessions
class SessionKeyring:
    """Content keys unwrapped for one session, held only in this process's memory."""

    def __init__(self, kms: LocalKMS, role: str, principal: str, store: EncryptedBranchStore | None = None) -> None:
        self.kms = kms
        self.store = store
        self.role = role
        self.principal = principal
        self.opened_at = time.time()
        self.last_used = self.opened_at
        self._keys: dict[str, bytearray] = {}
        self._lock = threading.Lock()

    def open_branch(self, branch: str) -> bytes:
        """The branch's plaintext when a store is attached, else its content key. Cached in memory."""
        with self._lock:
            self.last_used = time.time()
            if branch not in self._keys:
                self._keys[branch] = bytearray(self.kms.unwrap_branch_key(branch, self.role))
                self.kms.key_events.append({"event": "branch_opened", "branch": branch, "role": self.role,
                                            "principal": self.principal})
            ck = bytes(self._keys[branch])
        if self.store is not None:
            return self.store.open(branch, self.role)
        return ck

    def holds(self) -> list[str]:
        return sorted(self._keys)

    def wipe(self) -> int:
        with self._lock:
            n = len(self._keys)
            for buf in self._keys.values():
                for i in range(len(buf)):
                    buf[i] = 0
            self._keys.clear()
            return n

    def __del__(self) -> None:  # pragma: no cover - best effort
        try:
            self.wipe()
        except Exception:
            pass


class SessionKeyringRegistry:
    """One keyring per live session token; wiped on logout, expiry or shutdown."""

    def __init__(self, kms: LocalKMS, store: EncryptedBranchStore | None = None) -> None:
        self.kms = kms
        self.store = store
        self._rings: dict[str, SessionKeyring] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]

    def get_or_create(self, token: str, role: str, principal: str) -> SessionKeyring:
        key = self._digest(token)
        with self._lock:
            ring = self._rings.get(key)
            if ring is None or ring.role != role:
                if ring is not None:
                    ring.wipe()
                ring = self._rings[key] = SessionKeyring(self.kms, role, principal, self.store)
            return ring

    def wipe(self, token: str) -> bool:
        with self._lock:
            ring = self._rings.pop(self._digest(token), None)
        if ring is None:
            return False
        ring.wipe()
        return True

    def wipe_all(self) -> int:
        with self._lock:
            rings = list(self._rings.values())
            self._rings.clear()
        for r in rings:
            r.wipe()
        return len(rings)

    def status(self) -> dict:
        with self._lock:
            rings = list(self._rings.values())
        by_role: dict[str, int] = {}
        for r in rings:
            by_role[r.role] = by_role.get(r.role, 0) + 1
        return {"keyrings": len(rings), "by_role": by_role,
                "sessions": [{"principal": r.principal, "role": r.role, "branches": r.holds(),
                              "opened_at": r.opened_at, "last_used": r.last_used} for r in rings]}


# --------------------------------------------------------------------------- helpers
def vault_dir_for(cfg) -> Path:
    configured = getattr(getattr(cfg, "vault", None), "vault_dir", None)
    return Path(configured) if configured else Path(cfg.paths.security_dir).parent / "vault"


def seal_index_cache(cfg, kms: LocalKMS, store: EncryptedBranchStore, classifications, *, shred: bool = True) -> list[SealedBranchInfo]:
    """Seal every cached document index into the vault for the roles that may read it.

    With ``shred`` the plaintext ``index.json`` is overwritten and deleted. The files backend
    rebuilds a missing index from the knowledge-layer artefacts (normalized + chunks) when it
    must, so nothing is lost — but a deployment that wants no plaintext index on disk should keep
    ``RWB_VAULT=on`` so the backend loads from the vault instead of rebuilding.
    """
    cache_dir = Path(cfg.paths.cache_dir)
    out: list[SealedBranchInfo] = []
    if not cache_dir.exists():
        return out
    for doc_dir in sorted(cache_dir.iterdir()):
        idx = doc_dir / "index.json"
        if not doc_dir.is_dir() or not idx.exists():
            continue
        doc_id = doc_dir.name
        roles = [getattr(r, "value", str(r)) for r in classifications.readers_of(doc_id)]
        info = store.seal(doc_id, idx.read_bytes(), roles)
        out.append(info)
        if shred:
            EncryptedBranchStore.shred_plaintext(idx)
        logger.info("sealed %s for %s (%d bytes)", doc_id, ", ".join(roles) or "nobody", info.bytes_plain)
    return out


def load_sealed_index(cfg, store: EncryptedBranchStore, doc_id: str, role: str) -> dict:
    """The cached index of one document, decrypted into memory for ``role``."""
    return json.loads(store.open(doc_id, role).decode("utf-8"))


def vault_status(cfg, kms: LocalKMS, store: EncryptedBranchStore) -> dict:
    sealed = store.sealed_branches()
    cache_dir = Path(cfg.paths.cache_dir)
    plaintext = sorted(d.name for d in cache_dir.iterdir() if d.is_dir() and (d / "index.json").exists()) if cache_dir.exists() else []
    return {
        "vault_dir": str(store.dir),
        "sealed_branches": [{"branch": b, **{k: v for k, v in store.manifest(b).items() if k != "path"}} for b in sealed],
        "plaintext_indexes_on_disk": plaintext,
        "kms": kms.status(),
    }
