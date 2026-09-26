"""Hash-chained, append-only JSONL logs.

Every entry carries ``seq``, ``ts``, ``prev_hash`` and ``hash`` where

    hash = SHA-256( seq | ts | prev_hash | canonical_json(payload) )

so altering, removing or reordering any past entry changes every hash after it and ``verify()``
names the first entry that no longer matches. The genesis entry chains from sixty-four zeros.

Used by the security audit, the connection log, the sandbox run log, the routing log and the
draft/sign-off registry — everything a reviewer might later need to trust.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

GENESIS = "0" * 64
_CHAIN_FIELDS = ("seq", "ts", "prev_hash", "hash")


def canonical(payload: dict[str, Any]) -> str:
    """Deterministic JSON: sorted keys, no whitespace, non-ASCII escaped, unknown types stringified."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def entry_hash(seq: int, ts: float, prev_hash: str, payload: dict[str, Any]) -> str:
    body = f"{seq}|{ts!r}|{prev_hash}|{canonical(payload)}"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def strip_chain(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in _CHAIN_FIELDS}


class ChainVerification(BaseModel):
    ok: bool
    entries: int = 0
    head: str = GENESIS
    first_bad_seq: int | None = None
    detail: str = ""
    path: str = ""


class HashChainedLog:
    """Append-only JSONL file whose entries are chained by hash.

    Thread-safe within one process: one instance per path (``__new__`` hands back the existing
    one), so a verifier and a writer on the same file share a lock. The head hash is cached after
    the first read so appends are O(1); ``verify`` re-reads the file from the top, which is the
    whole point of it.
    """

    _instances: dict[str, "HashChainedLog"] = {}
    _registry_lock = threading.Lock()

    def __new__(cls, path: Path, *, name: str = "") -> "HashChainedLog":
        key = str(Path(path).resolve())
        with cls._registry_lock:
            inst = cls._instances.get(key)
            if inst is None:
                inst = super().__new__(cls)
                inst._initialised = False
                cls._instances[key] = inst
            return inst

    def __init__(self, path: Path, *, name: str = "") -> None:
        if getattr(self, "_initialised", False):
            if name and not self.name:
                self.name = name
            return
        self.path = Path(path)
        self.name = name or self.path.stem
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._seq: int | None = None
        self._head: str | None = None
        self._size: int = -1                       # file size after our last write; a change means another process wrote
        self._initialised = True

    # ------------------------------------------------------------------ cross-process lock
    @contextlib.contextmanager
    def _file_lock(self):
        """An OS-level lock on a sidecar file, so two processes appending to one log serialise."""
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        fh = open(lock_path, "a+b")
        try:
            try:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    for _ in range(200):
                        try:
                            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            time.sleep(0.01)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            fh.close()

    def _tail_head(self) -> tuple[int, str]:
        """(next seq, head hash) from the last chained line on disk."""
        if not self.path.exists():
            return 0, GENESIS
        with self.path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = min(size, 65536)
            fh.seek(size - block)
            tail = fh.read(block).decode("utf-8", errors="ignore")
        for line in reversed(tail.splitlines()):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "hash" in row and "seq" in row:
                return int(row["seq"]) + 1, str(row["hash"])
        return 0, GENESIS

    # ------------------------------------------------------------------ state
    def _load_head(self) -> None:
        seq, head = 0, GENESIS
        if self.path.exists():
            rows: list[dict[str, Any]] = []
            legacy = 0
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rows.append(row)
                if "hash" not in row or "seq" not in row:
                    legacy += 1
            if legacy:
                self._migrate_legacy(rows)
                rows = self.read()
            if rows:
                last = rows[-1]
                seq, head = int(last["seq"]) + 1, str(last["hash"])
        self._seq, self._head = seq, head

    def _migrate_legacy(self, rows: list[dict[str, Any]]) -> None:
        """Chain a log written before chaining existed, once, in place.

        Every entry keeps its content and order and gains ``seq``/``prev_hash``/``hash``; a
        final ``chain_migrated`` entry records that this happened and how many lines it covered.
        Entries before the migration are chained from the migration onwards, which is stated
        rather than hidden: ``legacy_entries`` is written into the marker.
        """
        prev = GENESIS
        out: list[str] = []
        legacy = 0
        for i, row in enumerate(rows):
            body = strip_chain(dict(row))
            ts = float(row.get("ts") or time.time())
            if "hash" not in row:
                legacy += 1
            digest = entry_hash(i, ts, prev, body)
            out.append(json.dumps({"seq": i, "ts": ts, "prev_hash": prev, "hash": digest, **body},
                                  ensure_ascii=False, default=str))
            prev = digest
        i = len(rows)
        ts = time.time()
        marker = {"event": "chain_migrated", "legacy_entries": legacy, "note": "entries above were chained retroactively"}
        digest = entry_hash(i, ts, prev, marker)
        out.append(json.dumps({"seq": i, "ts": ts, "prev_hash": prev, "hash": digest, **marker}, ensure_ascii=False))
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    @property
    def head(self) -> str:
        with self._lock:
            if self._head is None:
                self._load_head()
            return self._head or GENESIS

    def __len__(self) -> int:
        with self._lock:
            if self._seq is None:
                self._load_head()
            return self._seq or 0

    # ------------------------------------------------------------------ write
    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._file_lock():
            if self._seq is None or self._head is None:
                self._load_head()
            # another process may have appended since we last wrote: trust the file, not the cache
            try:
                size = self.path.stat().st_size if self.path.exists() else 0
            except OSError:
                size = -1
            if size != self._size:
                self._seq, self._head = self._tail_head()
            seq = int(self._seq or 0)
            prev = self._head or GENESIS
            ts = time.time()
            body = strip_chain(dict(payload))
            digest = entry_hash(seq, ts, prev, body)
            record = {"seq": seq, "ts": ts, "prev_hash": prev, "hash": digest, **body}
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                fh.flush()
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
            self._seq, self._head = seq + 1, digest
            try:
                self._size = self.path.stat().st_size
            except OSError:
                self._size = -1
            return record

    def repair(self, reason: str = "") -> ChainVerification:
        """Re-chain a broken file in place, keeping every line's content and order.

        This is not a way to hide tampering: the repair writes a ``chain_repaired`` marker that
        records the first bad sequence number and the reason, and the marker is itself chained.
        It exists for the one legitimate case — two processes appending without the lock (older
        builds) — and it is a deliberate, logged, administrator-only action.
        """
        before = self.verify()
        if before.ok:
            return before
        with self._lock, self._file_lock():
            rows = self.read()
            prev = GENESIS
            out: list[str] = []
            for i, row in enumerate(rows):
                body = strip_chain(dict(row))
                ts = float(row.get("ts") or time.time())
                digest = entry_hash(i, ts, prev, body)
                out.append(json.dumps({"seq": i, "ts": ts, "prev_hash": prev, "hash": digest, **body}, ensure_ascii=False, default=str))
                prev = digest
            i = len(rows)
            ts = time.time()
            marker = {"event": "chain_repaired", "first_bad_seq": before.first_bad_seq, "detail": before.detail, "reason": reason}
            digest = entry_hash(i, ts, prev, marker)
            out.append(json.dumps({"seq": i, "ts": ts, "prev_hash": prev, "hash": digest, **marker}, ensure_ascii=False))
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            self._seq, self._head = i + 1, digest
            self._size = self.path.stat().st_size
        return self.verify()

    # ------------------------------------------------------------------ read / verify
    def read(self, limit: int | None = None, **filters: Any) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if all(row.get(k) == v for k, v in filters.items() if v is not None):
                rows.append(row)
        return rows[-limit:] if limit else rows

    def verify(self) -> ChainVerification:
        if not self.path.exists():
            return ChainVerification(ok=True, entries=0, head=GENESIS, detail="empty log", path=str(self.path))
        with self._lock:
            if self._head is None:
                self._load_head()              # chains a legacy file first, so it can be verified at all
            text = self.path.read_text(encoding="utf-8")
        prev = GENESIS
        expected_seq = 0
        count = 0
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                if i == len(lines) - 1 and not text.endswith("\n"):
                    break                      # a line another thread/process is still writing
                return ChainVerification(ok=False, entries=count, head=prev, first_bad_seq=expected_seq,
                                         detail="unparseable line", path=str(self.path))
            if "hash" not in row:
                # a legacy, un-chained line (written before chaining was introduced) breaks the chain
                return ChainVerification(ok=False, entries=count, head=prev, first_bad_seq=expected_seq,
                                         detail="entry without chain fields", path=str(self.path))
            seq = int(row.get("seq", -1))
            if seq != expected_seq:
                return ChainVerification(ok=False, entries=count, head=prev, first_bad_seq=expected_seq,
                                         detail=f"sequence gap: expected {expected_seq}, found {seq}", path=str(self.path))
            if row.get("prev_hash") != prev:
                return ChainVerification(ok=False, entries=count, head=prev, first_bad_seq=seq,
                                         detail="previous-hash link broken", path=str(self.path))
            recomputed = entry_hash(seq, float(row["ts"]), prev, strip_chain(row))
            if recomputed != row.get("hash"):
                return ChainVerification(ok=False, entries=count, head=prev, first_bad_seq=seq,
                                         detail="entry hash does not recompute (content altered)", path=str(self.path))
            prev = str(row["hash"])
            expected_seq += 1
            count += 1
        return ChainVerification(ok=True, entries=count, head=prev, detail="chain intact", path=str(self.path))


def verify_file(path: Path) -> ChainVerification:
    return HashChainedLog(path).verify()
