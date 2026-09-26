"""Vaulted knowledge branches: decrypted into memory only for a session that can unwrap them.

With ``RWB_VAULT=on`` the knowledge layer's branch indexes are not read from the plaintext cache.
Each branch sits sealed in ``data/workbench/vault/<branch>/index.enc`` (see ``vault.py``) and is
loaded like this:

    a session signs in  ->  its keyring unwraps the content keys its *role* holds
                        ->  each such branch is decrypted into this process's memory and joins
                            the composite knowledge service
    the last session that could open a branch signs out
                        ->  the branch is dropped from memory again

So before an administrator signs in, the CDU manual is not in the process at all — not filtered,
not withheld, simply absent — and a role that holds no wrapped copy of a branch key cannot make
it appear however it phrases a question. The ordinary guard still applies on top for per-session
scoping; this layer decides what plaintext exists in the process to begin with.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from workbench.security.vault import EncryptedBranchStore, LocalKMS, SessionKeyringRegistry, VaultAccessError

logger = logging.getLogger(__name__)


class VaultedBranches:
    def __init__(self, cfg, composite, kms: LocalKMS, store: EncryptedBranchStore, keyrings: SessionKeyringRegistry,
                 classifications) -> None:
        self.cfg = cfg
        self.composite = composite
        self.kms = kms
        self.store = store
        self.keyrings = keyrings
        self.classifications = classifications
        self.loaded: dict[str, object] = {}                 # branch -> IndexStore in memory
        self.opened_by: dict[str, set[str]] = {}            # branch -> principals who opened it
        self.opened_at: dict[str, float] = {}
        self._lock = threading.Lock()
        self.events: list[dict] = []

    # ------------------------------------------------------------------ what is sealed
    def sealed(self) -> list[str]:
        return self.store.sealed_branches()

    def readers_of(self, branch: str) -> list[str]:
        try:
            return self.kms.wrapped_roles(branch)
        except Exception:
            return []

    # ------------------------------------------------------------------ open / close
    def _load_branch(self, branch: str, plaintext: bytes):
        from workbench.services.backends.files_backend import load_embeddings
        from workbench.services.index.builder import DocumentIndex
        from workbench.services.index.store import IndexStore

        raw = json.loads(plaintext.decode("utf-8"))
        idx = DocumentIndex.model_validate(raw["index"] if "index" in raw else raw)
        embeddings = None
        if self.cfg.retrieval.use_vectors:
            emb = load_embeddings(self.cfg, branch)
            embeddings = {branch: emb} if emb else None
        r = self.cfg.retrieval
        return IndexStore([idx], embeddings, embedding_model=r.embedding_model, embedding_device=r.embedding_device,
                          reranker_model=None, use_vectors=r.use_vectors, use_reranker=False, rrf_k=r.rrf_k)

    def open_for(self, token: str | None, principal) -> list[str]:
        """Unwrap and load every sealed branch this principal's role holds a key for. Returns what was opened now."""
        if not token or not getattr(principal, "authenticated", False):
            return []
        role = principal.role.value if hasattr(principal.role, "value") else str(principal.role)
        keyring = self.keyrings.get_or_create(token, role, principal.username)
        opened: list[str] = []
        for branch in self.sealed():
            if role not in self.readers_of(branch):
                continue
            with self._lock:
                if branch in self.loaded:
                    self.opened_by.setdefault(branch, set()).add(principal.username)
                    continue
                t0 = time.time()
                try:
                    plaintext = keyring.open_branch(branch)
                except VaultAccessError as exc:
                    logger.info("branch %s not opened for %s: %s", branch, principal.username, exc)
                    continue
                try:
                    backend = self._load_branch(branch, plaintext)
                finally:
                    plaintext = b""                      # drop the reference; the keyring keeps only the CK
                self.loaded[branch] = backend
                self.opened_by.setdefault(branch, set()).add(principal.username)
                self.opened_at[branch] = time.time()
                self.composite.add(backend)
                opened.append(branch)
                self.events.append({"ts": time.time(), "event": "branch_opened", "branch": branch, "by": principal.username,
                                    "role": role, "ms": int((time.time() - t0) * 1000)})
                logger.info("vault: branch %s decrypted into memory for %s (%s) in %.1fs", branch, principal.username, role, time.time() - t0)
        return opened

    def close_orphans(self, active_roles: set[str]) -> list[str]:
        """Drop from memory every branch that no signed-in role can still open."""
        closed: list[str] = []
        with self._lock:
            for branch in list(self.loaded):
                if any(r in active_roles for r in self.readers_of(branch)):
                    continue
                backend = self.loaded.pop(branch)
                self.composite.remove(backend)
                self.opened_by.pop(branch, None)
                self.opened_at.pop(branch, None)
                closed.append(branch)
                self.events.append({"ts": time.time(), "event": "branch_closed", "branch": branch, "reason": "no session with a key remains"})
                logger.info("vault: branch %s dropped from memory (no session with a key remains)", branch)
        return closed

    def close_all(self) -> list[str]:
        return self.close_orphans(set())

    # ------------------------------------------------------------------ reporting
    def status(self) -> dict:
        rows = []
        for branch in self.sealed():
            rows.append({"branch": branch, "sealed": True, "in_memory": branch in self.loaded,
                         "roles_with_key": self.readers_of(branch),
                         "opened_by": sorted(self.opened_by.get(branch, ())),
                         "opened_at": self.opened_at.get(branch)})
        return {"enabled": True, "branches": rows, "in_memory": sorted(self.loaded),
                "recent_events": self.events[-20:]}
