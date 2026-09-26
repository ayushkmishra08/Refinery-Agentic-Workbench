"""The code sandbox: hardened, ephemeral, no-egress execution of untrusted Python.

- ``runner``    SandboxRunner — one fresh working directory per run, destroyed afterwards;
                docker backend (``--network none``, dropped capabilities, read-only root) when a
                daemon is present, otherwise an isolated subprocess (``python -I -S -B``) whose
                preamble neuters sockets, refuses dangerous imports, confines writes to the
                working directory and is held under a Windows job object / POSIX rlimits.
- ``verify``    static analysis (AST bans + pyflakes) and the meaning of "verified":
                static pass AND the task's own tests passed — not "ran without crashing".
- ``manifest``  pinned, checksum-verified vendored dependencies; nothing is fetched at run time.
- ``runlog``    hash-chained log of every run (stdout/stderr digests, resource usage, file diffs).
"""
from workbench.sandbox.runner import SandboxLimits, SandboxResult, SandboxRunner
from workbench.sandbox.runlog import SandboxRunLog

__all__ = ["SandboxLimits", "SandboxResult", "SandboxRunner", "SandboxRunLog"]
