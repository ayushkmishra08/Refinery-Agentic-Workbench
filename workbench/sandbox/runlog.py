"""Hash-chained log of every sandbox run.

One line per run: what ran (code and test digests), under which backend and manifest, how it
ended (exit code, limit hit, verified), what it cost (wall time, peak RSS, CPU seconds), what it
wrote (file hashes) and what it tried to reach (blocked egress destinations). Output is logged
by digest, not content, so the log stays small and leaks nothing; the run result carries the
text. Chained so a past run cannot be altered after the fact.
"""
from __future__ import annotations

from pathlib import Path

from workbench.sovereignty.hashchain import ChainVerification, HashChainedLog


class SandboxRunLog:
    def __init__(self, path: Path) -> None:
        self.chain = HashChainedLog(Path(path), name="sandbox")

    @property
    def path(self) -> Path:
        return self.chain.path

    def record(self, payload: dict) -> dict:
        return self.chain.append(payload)

    def read(self, limit: int | None = 50) -> list[dict]:
        return self.chain.read(limit)

    def verify(self) -> ChainVerification:
        return self.chain.verify()

    def __len__(self) -> int:
        return len(self.chain)
