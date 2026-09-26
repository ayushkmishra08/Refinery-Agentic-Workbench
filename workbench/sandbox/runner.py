"""SandboxRunner: one fresh, bounded, network-less working directory per task, destroyed after.

Two backends share one contract:

``docker``      ``docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges
                --read-only --tmpfs /tmp --memory … --cpus 1 --pids-limit …`` over a pre-vendored
                image. No network namespace at all, not a firewalled one. Used when a daemon
                answers ``docker info`` within three seconds.
``subprocess``  ``python -s -S -B -P main.py`` (no site-packages, no user site, safe path, an
                environment built from scratch with a fixed hash seed) in a temporary directory, under a Windows job object (process
                memory, CPU time, one process, kill-on-close) or POSIX rlimits, with a preamble
                that runs *before* the task's code and:
                  - replaces every socket constructor with one that raises and records the
                    destination (``_egress.json``), and blocks ssl/urllib/http.client;
                  - installs an import hook refusing subprocess, ctypes, multiprocessing, the
                    HTTP client libraries, signal, pty, ...;
                  - stubs the ``os`` functions that spawn, kill, delete or re-permission;
                  - confines ``open()``/``io.open()`` write modes to the working directory and
                    caps the bytes written;
                  - clears the environment, fixes ``PYTHONHASHSEED`` and seeds ``random``.

Either way the working directory is created for the run and removed at its end; files the task
produced come back in the result as hashes (and, up to a size cap, as base64 artifacts) — nothing
carries into the next run. Every run is appended to the hash-chained run log when one is given.

"Verified" means the static analysis passed and the task's own tests passed. It never means
"ran without crashing".
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from workbench.sandbox.manifest import BANNED_IMPORTS, VENDOR_DIR, ManifestError, verify_manifest
from workbench.sandbox.runlog import SandboxRunLog
from workbench.sandbox.verify import analyse

logger = logging.getLogger(__name__)

_MAIN = "main.py"
_EGRESS_FILE = "_egress.json"
_WRITES_FILE = "_writes.json"
_TESTS_MARK = "__SANDBOX_TESTS__"
#: Windows exit codes the job object produces when it terminates the process
_WIN_QUOTA_EXIT = {1816, 0xC0000017 - (1 << 32), 0xC0000017, 0xC000012A - (1 << 32), 0xC000012A}
_ARTIFACT_CAP = 5 * 1024 * 1024


class SandboxLimits(BaseModel):
    timeout_seconds: float = 20
    memory_mb: int = 512
    cpu_seconds: float = 20
    max_output_bytes: int = 200_000
    max_disk_write_mb: int = 20
    max_processes: int = 1


class WrittenFile(BaseModel):
    path: str
    sha256: str
    bytes: int


class SandboxResult(BaseModel):
    run_id: str
    task_id: str = ""
    backend: str
    ok: bool = False
    verified: bool = False
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    limit_hit: str | None = None
    duration_ms: int = 0
    peak_rss_mb: float = 0.0
    cpu_seconds: float = 0.0
    files_written: list[WrittenFile] = Field(default_factory=list)
    files_changed: list[dict] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict, description="workdir file -> base64 content, capped at 5 MB in total")
    workdir_destroyed: bool = False
    egress_attempts: list[str] = Field(default_factory=list)
    static_analysis: dict | None = None
    verification: dict | None = None
    python_version: str = ""
    manifest_hash: str = ""
    job_object: bool = False
    log_hash: str = ""
    error: str | None = None


# --------------------------------------------------------------------------- the preamble
# Runs inside the child before the task's code. Tokens in __UPPER__ are substituted by the runner.
PREAMBLE = r'''
import sys as _sb_sys, os as _sb_os, io as _sb_io, builtins as _sb_builtins, json as _sb_json
_SB_WORKDIR = _sb_os.path.realpath(_sb_os.getcwd())
_SB_MAX_WRITE = __MAX_WRITE__
_SB_VENDOR = __VENDOR__
_SB_BANNED = __BANNED__
if _SB_VENDOR:
    _sb_sys.path.insert(0, _SB_VENDOR)
_sb_sys.setrecursionlimit(2000)

# ---- 1. no sockets ---------------------------------------------------------------------------
import socket as _sb_socket
def _sb_record_egress(dest):
    try:
        p = _sb_os.path.join(_SB_WORKDIR, "__EGRESS__")
        rows = []
        if _sb_os.path.exists(p):
            with _sb_io.open(p, "r", encoding="utf-8") as fh:
                rows = _sb_json.load(fh)
        rows.append(str(dest))
        with _sb_io.open(p, "w", encoding="utf-8") as fh:
            _sb_json.dump(rows, fh)
    except Exception:
        pass
class _SbNoSocket:
    def __init__(self, *a, **k):
        _sb_record_egress("socket()")
        raise PermissionError("sandbox: network egress is disabled")
def _sb_no_connect(address=None, *a, **k):
    _sb_record_egress(address)
    raise PermissionError("sandbox: network egress is disabled (%r)" % (address,))
def _sb_no_getaddrinfo(host=None, port=None, *a, **k):
    _sb_record_egress("%s:%s" % (host, port))
    raise PermissionError("sandbox: name resolution is disabled (%r)" % (host,))
_sb_socket.socket = _SbNoSocket
_sb_socket.SocketType = _SbNoSocket
_sb_socket.create_connection = _sb_no_connect
_sb_socket.create_server = _sb_no_connect
_sb_socket.getaddrinfo = _sb_no_getaddrinfo
_sb_socket.gethostbyname = _sb_no_getaddrinfo
_sb_socket.socketpair = _sb_no_connect
_sb_socket.fromfd = _sb_no_connect

# ---- 2. import hook -----------------------------------------------------------------------------
class _SbImportGuard:
    @staticmethod
    def find_spec(name, path=None, target=None):
        for b in _SB_BANNED:
            if name == b or name.startswith(b + "."):
                raise ImportError("sandbox: import of %r is not permitted" % name)
        return None
_sb_sys.meta_path.insert(0, _SbImportGuard())
for _sb_name in list(_sb_sys.modules):
    for _sb_b in _SB_BANNED:
        if _sb_name == _sb_b or _sb_name.startswith(_sb_b + "."):
            del _sb_sys.modules[_sb_name]

# ---- 3. environment -------------------------------------------------------------------------
_sb_os.environ.clear()
_sb_os.environ["SANDBOX"] = "1"
_sb_os.environ["PYTHONHASHSEED"] = "0"

# ---- 4. os stubs ---------------------------------------------------------------------------
def _sb_refuse(name):
    def _f(*a, **k):
        raise PermissionError("sandbox: os.%s is not permitted" % name)
    _f.__name__ = name
    return _f
for _sb_fn in ("system", "popen", "execv", "execve", "execl", "execle", "execlp", "execlpe", "execvp", "execvpe",
               "spawnl", "spawnle", "spawnv", "spawnve", "spawnlp", "spawnlpe", "spawnvp", "spawnvpe", "fork", "forkpty",
               "kill", "killpg", "remove", "unlink", "rmdir", "removedirs", "rename", "replace", "renames", "chmod", "chown",
               "lchown", "symlink", "link", "startfile", "putenv", "unsetenv", "setuid", "setgid", "chroot", "truncate",
               "posix_spawn", "posix_spawnp", "abort", "_exit"):
    for _sb_mod in (_sb_os, _sb_sys.modules.get(_sb_os.name)):
        if _sb_mod is not None and hasattr(_sb_mod, _sb_fn):
            try:
                setattr(_sb_mod, _sb_fn, _sb_refuse(_sb_fn))
            except Exception:
                pass

try:
    import _winapi as _sb_winapi
    for _sb_fn in ("CreateProcess", "OpenProcess", "TerminateProcess", "CreateJunction", "CreateNamedPipe",
                   "ConnectNamedPipe", "CreateFile", "CreatePipe", "DuplicateHandle", "ExitProcess"):
        if hasattr(_sb_winapi, _sb_fn):
            try:
                setattr(_sb_winapi, _sb_fn, _sb_refuse("_winapi." + _sb_fn))
            except Exception:
                pass
except ImportError:
    pass

# ---- 5. confined writes -----------------------------------------------------------------------
_sb_written = {"bytes": 0}
_sb_real_open = _sb_io.open
def _sb_inside(path):
    try:
        real = _sb_os.path.realpath(_sb_os.fspath(path))
    except Exception:
        return False
    return real == _SB_WORKDIR or real.startswith(_SB_WORKDIR + _sb_os.sep)
class _SbCountingWriter:
    def __init__(self, fh):
        self._fh = fh
    def write(self, data):
        n = len(data) if isinstance(data, (bytes, bytearray)) else len(str(data).encode("utf-8", "replace"))
        _sb_written["bytes"] += n
        if _sb_written["bytes"] > _SB_MAX_WRITE:
            raise PermissionError("sandbox: disk write limit exceeded")
        return self._fh.write(data)
    def writelines(self, lines):
        for line in lines:
            self.write(line)
    def __getattr__(self, item):
        return getattr(self._fh, item)
    def __enter__(self):
        self._fh.__enter__()
        return self
    def __exit__(self, *a):
        return self._fh.__exit__(*a)
    def __iter__(self):
        return iter(self._fh)
def _sb_open(file, mode="r", *args, **kwargs):
    if isinstance(file, int):
        return _sb_real_open(file, mode, *args, **kwargs)
    if any(ch in mode for ch in "wax+"):
        if not _sb_inside(file):
            raise PermissionError("sandbox: writing outside the working directory is not permitted (%r)" % (file,))
        return _SbCountingWriter(_sb_real_open(file, mode, *args, **kwargs))
    return _sb_real_open(file, mode, *args, **kwargs)
_sb_builtins.open = _sb_open
_sb_io.open = _sb_open
try:
    _sb_real_mkdir = _sb_os.mkdir
    def _sb_mkdir(path, *a, **k):
        if not _sb_inside(path):
            raise PermissionError("sandbox: mkdir outside the working directory")
        return _sb_real_mkdir(path, *a, **k)
    _sb_os.mkdir = _sb_mkdir
    _sb_real_makedirs = _sb_os.makedirs
    def _sb_makedirs(path, *a, **k):
        if not _sb_inside(path):
            raise PermissionError("sandbox: makedirs outside the working directory")
        return _sb_real_makedirs(path, *a, **k)
    _sb_os.makedirs = _sb_makedirs
except Exception:
    pass

# ---- 6. determinism -----------------------------------------------------------------------
import random as _sb_random
_sb_random.seed(0)
try:
    import numpy as _sb_np
    _sb_np.random.seed(0)
except Exception:
    pass
try:
    del _sb_name, _sb_b, _sb_fn, _sb_mod
except NameError:
    pass
# ---- end of sandbox preamble ---------------------------------------------------------------
'''.replace("__EGRESS__", _EGRESS_FILE)

_TEST_RUNNER = r'''

# ---- sandbox test runner ----------------------------------------------------------------------
def _sb_run_tests():
    import sys as _s
    names = [n for n in list(globals()) if n.startswith("test_") and callable(globals()[n])]
    failed = 0
    for n in names:
        try:
            globals()[n]()
            print("PASS " + n)
        except BaseException as e:
            failed += 1
            print("FAIL " + n + ": " + repr(e))
    print("__TESTS__ %d %d" % (len(names), len(names) - failed))
    _s.stdout.flush()
    _s.exit(1 if failed else 0)
_sb_run_tests()
'''


# --------------------------------------------------------------------------- helpers
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _truncate(text: str, cap: int) -> str:
    if len(text.encode("utf-8", "replace")) <= cap:
        return text
    return text.encode("utf-8", "replace")[:cap].decode("utf-8", "replace") + f"\n...[output truncated at {cap} bytes]"


def _rmtree(path: Path) -> bool:
    for attempt in range(5):
        try:
            shutil.rmtree(path, ignore_errors=False)
            return True
        except FileNotFoundError:
            return True
        except Exception:
            time.sleep(0.1 * (attempt + 1))
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()


def _kill_tree(pid: int) -> None:
    try:
        import psutil

        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
        parent.kill()
    except Exception:
        pass


def docker_available(timeout: float = 3.0) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False


class _Sampler:
    """Peak RSS / CPU seconds of a child, sampled every 50 ms; kills it when RSS exceeds the cap."""

    def __init__(self, pid: int, memory_cap_mb: int | None) -> None:
        self.pid = pid
        self.cap = memory_cap_mb
        self.peak_rss = 0
        self.cpu = 0.0
        self.killed_for_memory = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> "_Sampler":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def _loop(self) -> None:
        try:
            import psutil

            proc = psutil.Process(self.pid)
        except Exception:
            return
        while not self._stop.is_set():
            try:
                rss = proc.memory_info().rss
                t = proc.cpu_times()
                self.peak_rss = max(self.peak_rss, rss)
                self.cpu = float(t.user + t.system)
                if self.cap and rss > self.cap * 1024 * 1024 * 1.5:
                    self.killed_for_memory = True
                    _kill_tree(self.pid)
                    return
            except Exception:
                return
            self._stop.wait(0.05)


def _job_object(limits: SandboxLimits):
    """A Windows job object carrying the resource caps, or None when unavailable."""
    if os.name != "nt":
        return None
    try:
        import win32job

        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        basic = info["BasicLimitInformation"]
        basic["LimitFlags"] = (win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY | win32job.JOB_OBJECT_LIMIT_PROCESS_TIME
                               | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                               | win32job.JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION)
        basic["PerProcessUserTimeLimit"] = int(limits.cpu_seconds * 10_000_000)
        basic["ActiveProcessLimit"] = max(1, int(limits.max_processes))
        info["ProcessMemoryLimit"] = int(limits.memory_mb) * 1024 * 1024
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        return job
    except Exception as exc:
        logger.warning("job object unavailable: %s", exc)
        return None


def _assign_job(job, pid: int) -> bool:
    try:
        import win32api
        import win32con
        import win32job

        handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, pid)
        win32job.AssignProcessToJobObject(job, handle)
        return True
    except Exception as exc:
        logger.warning("could not assign sandbox process to job object: %s", exc)
        return False


def _posix_limits(limits: SandboxLimits):
    def apply() -> None:
        try:
            import resource

            mem = int(limits.memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
            cpu = int(limits.cpu_seconds) + 1
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
            fsize = int(limits.max_disk_write_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
            try:
                resource.setrlimit(resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes))
            except Exception:
                pass
        except Exception:
            pass
    return apply


# --------------------------------------------------------------------------- the runner
class SandboxRunner:
    def __init__(self, root_dir: Path, limits: SandboxLimits | None = None, backend: str = "auto",
                 python: str | None = None, log: SandboxRunLog | None = None, vendor_dir: Path | None = None,
                 docker_image: str = "refinery-sandbox:latest", docker_fallback_image: str = "python:3.12-slim") -> None:
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.limits = limits or SandboxLimits()
        # The venv's python.exe on Windows is a launcher that spawns the real interpreter as a
        # second process, which a one-process job object refuses. Isolated mode never sees the
        # venv's site-packages anyway, so the base interpreter is the right one.
        self.python = python or getattr(sys, "_base_executable", None) or sys.executable
        self.log = log
        self.vendor_dir = Path(vendor_dir) if vendor_dir else VENDOR_DIR
        self.docker_image = docker_image
        self.docker_fallback_image = docker_fallback_image
        if backend == "auto":
            backend = "docker" if docker_available() else "subprocess"
        self.backend = backend
        self.manifest_hash, self.vendor_path, self.manifest_error = self._load_manifest()

    # ------------------------------------------------------------------ manifest
    def _load_manifest(self) -> tuple[str, str | None, str | None]:
        try:
            manifest = verify_manifest(self.vendor_dir)
        except ManifestError as exc:
            logger.warning("vendored dependencies refused: %s", exc)
            return "invalid", None, str(exc)
        vendor = str(self.vendor_dir.resolve()) if manifest.vendor else None
        return manifest.hash, vendor, None

    # ------------------------------------------------------------------ composition
    def _preamble(self, workdir: Path) -> str:
        return (PREAMBLE.replace("__MAX_WRITE__", str(int(self.limits.max_disk_write_mb) * 1024 * 1024))
                .replace("__VENDOR__", repr(self.vendor_path or ""))
                .replace("__BANNED__", repr(tuple(BANNED_IMPORTS))))

    def _write_workdir(self, code: str, inputs: dict[str, bytes | str] | None, tests: str | None) -> tuple[Path, dict[str, str], set[str]]:
        workdir = Path(tempfile.mkdtemp(prefix="sbx-", dir=self.root))
        input_hashes: dict[str, str] = {}
        reserved = {_MAIN, _EGRESS_FILE, _WRITES_FILE}
        for name, data in (inputs or {}).items():
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"input path must be relative and inside the sandbox: {name!r}")
            dest = workdir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
            dest.write_bytes(raw)
            input_hashes[rel.as_posix()] = _sha256_bytes(raw)
            reserved.add(rel.as_posix())
        body = self._preamble(workdir) + "\n" + code
        if tests is not None:
            body += "\n\n# ---- task tests ----\n" + tests + _TEST_RUNNER
        (workdir / _MAIN).write_text(body, encoding="utf-8")
        return workdir, input_hashes, reserved

    # ------------------------------------------------------------------ execution
    def _exec_subprocess(self, workdir: Path, result: SandboxResult) -> None:
        # -I would imply -E, which ignores PYTHONHASHSEED and breaks determinism; the environment
        # is built from scratch here, so -s -S -P with an explicit hash seed gives the same isolation.
        env = {"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
               "PYTHONNOUSERSITE": "1", "PYTHONSAFEPATH": "1", "SANDBOX": "1"}
        if os.name == "nt":
            for key in ("SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "COMSPEC"):
                if key in os.environ:
                    env[key] = os.environ[key]
        cmd = [self.python, "-s", "-S", "-B", "-P", _MAIN]
        kwargs: dict = {}
        job = None
        if os.name == "nt":
            job = _job_object(self.limits)
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["preexec_fn"] = _posix_limits(self.limits)
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=str(workdir), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        if job is not None:
            result.job_object = _assign_job(job, proc.pid)
        sampler = _Sampler(proc.pid, self.limits.memory_mb).start()
        try:
            out, err = proc.communicate(timeout=self.limits.timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            try:
                out, err = proc.communicate(timeout=5)
            except Exception:
                out, err = b"", b""
            result.timed_out = True
            result.limit_hit = "timeout"
        finally:
            sampler.stop()
            if job is not None:
                try:
                    import win32api

                    win32api.CloseHandle(job)
                except Exception:
                    pass
        result.duration_ms = int((time.time() - t0) * 1000)
        result.exit_code = proc.returncode
        result.stdout = _truncate(out.decode("utf-8", "replace"), self.limits.max_output_bytes)
        result.stderr = _truncate(err.decode("utf-8", "replace"), self.limits.max_output_bytes)
        result.peak_rss_mb = round(sampler.peak_rss / (1024 * 1024), 1)
        result.cpu_seconds = round(sampler.cpu, 3)
        if result.limit_hit is None:
            if sampler.killed_for_memory or "MemoryError" in result.stderr or (proc.returncode in _WIN_QUOTA_EXIT and result.cpu_seconds < self.limits.cpu_seconds):
                result.limit_hit = "memory"
            elif proc.returncode in _WIN_QUOTA_EXIT or result.cpu_seconds >= self.limits.cpu_seconds:
                result.limit_hit = "cpu"
            elif "disk write limit exceeded" in result.stderr:
                result.limit_hit = "disk"
            elif "network egress is disabled" in result.stderr or "name resolution is disabled" in result.stderr:
                result.limit_hit = "egress"

    def _exec_docker(self, workdir: Path, result: SandboxResult, requirements: list[str] | None) -> None:
        image = self.docker_image
        check = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
        if check.returncode != 0:
            image = self.docker_fallback_image
            if requirements:
                result.error = f"unvendored requirement(s) {requirements}: the pre-vendored image {self.docker_image} is not present and nothing is fetched at run time"
        cmd = ["docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--read-only", "--tmpfs", "/tmp", "--memory", f"{self.limits.memory_mb}m", "--cpus", "1",
               "--pids-limit", str(max(1, self.limits.max_processes) + 1), "-e", "PYTHONHASHSEED=0", "-e", "PYTHONDONTWRITEBYTECODE=1",
               "-v", f"{workdir}:/work:rw", "-e", "PYTHONNOUSERSITE=1", "-e", "PYTHONSAFEPATH=1",
               "-w", "/work", image, "python", "-s", "-S", "-B", "-P", _MAIN]
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=self.limits.timeout_seconds + 15)
            result.exit_code = proc.returncode
            out, err = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            result.timed_out = True
            result.limit_hit = "timeout"
            result.exit_code = None
            out, err = exc.stdout or b"", exc.stderr or b""
        result.duration_ms = int((time.time() - t0) * 1000)
        result.stdout = _truncate(out.decode("utf-8", "replace"), self.limits.max_output_bytes)
        result.stderr = _truncate(err.decode("utf-8", "replace"), self.limits.max_output_bytes)
        if result.limit_hit is None:
            if result.exit_code == 137 or "MemoryError" in result.stderr:
                result.limit_hit = "memory"
            elif "disk write limit exceeded" in result.stderr:
                result.limit_hit = "disk"

    # ------------------------------------------------------------------ collection
    def _collect(self, workdir: Path, reserved: set[str], input_hashes: dict[str, str], result: SandboxResult) -> None:
        egress = workdir / _EGRESS_FILE
        if egress.exists():
            try:
                result.egress_attempts = [str(x) for x in json.loads(egress.read_text(encoding="utf-8"))]
            except Exception:
                result.egress_attempts = ["(unreadable egress record)"]
            if result.limit_hit is None and result.egress_attempts:
                result.limit_hit = "egress"
        budget = _ARTIFACT_CAP
        for p in sorted(workdir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(workdir).as_posix()
            if rel in reserved or rel.startswith("__pycache__"):
                if rel in input_hashes:
                    digest = _sha256_file(p)
                    result.files_changed.append({"path": rel, "status": "modified" if digest != input_hashes[rel] else "unchanged"})
                continue
            size = p.stat().st_size
            digest = _sha256_file(p)
            result.files_written.append(WrittenFile(path=rel, sha256=digest, bytes=size))
            if size <= budget:
                result.artifacts[rel] = base64.b64encode(p.read_bytes()).decode("ascii")
                budget -= size
        for rel in input_hashes:
            if not (workdir / rel).exists():
                result.files_changed.append({"path": rel, "status": "deleted"})

    @staticmethod
    def _parse_tests(result: SandboxResult) -> dict:
        total, passed = 0, 0
        failures = [line[5:] for line in result.stdout.splitlines() if line.startswith("FAIL ")]
        for line in result.stdout.splitlines():
            if line.startswith("__TESTS__ "):
                parts = line.split()
                total, passed = int(parts[1]), int(parts[2])
        ran = any(line.startswith("__TESTS__") for line in result.stdout.splitlines())
        ok = ran and result.exit_code == 0 and passed == total and result.limit_hit is None
        if not ran and result.stderr:
            failures.append(result.stderr.strip().splitlines()[-1][:300])
        return {"tests_total": total, "tests_passed": passed, "failures": failures, "passed": ok, "ran": ran}

    # ------------------------------------------------------------------ the run
    def _single(self, code: str, inputs, tests, task_id: str, requirements) -> SandboxResult:
        result = SandboxResult(run_id=f"sbx-{uuid.uuid4().hex[:10]}", task_id=task_id, backend=self.backend,
                               python_version=sys.version.split()[0], manifest_hash=self.manifest_hash)
        workdir: Path | None = None
        try:
            workdir, input_hashes, reserved = self._write_workdir(code, inputs, tests)
            if self.backend == "docker":
                self._exec_docker(workdir, result, requirements)
            else:
                if requirements:
                    missing = [r for r in requirements if not self._vendored(r)]
                    if missing:
                        result.error = f"unvendored requirement(s) {missing}: nothing is fetched at run time; pin them in the vendor manifest"
                self._exec_subprocess(workdir, result)
            self._collect(workdir, reserved, input_hashes, result)
        except Exception as exc:
            logger.exception("sandbox run failed")
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            if workdir is not None:
                result.workdir_destroyed = _rmtree(workdir)
        result.ok = result.exit_code == 0 and result.limit_hit is None and not result.timed_out and result.error is None
        return result

    def _vendored(self, requirement: str) -> bool:
        if not self.vendor_path:
            return False
        name = requirement.split("==")[0].split(">=")[0].strip().lower().replace("-", "_")
        base = Path(self.vendor_path)
        return (base / name).exists() or (base / f"{name}.py").exists()

    def run(self, code: str, *, inputs: dict[str, bytes | str] | None = None, tests: str | None = None,
            task_id: str = "", requirements: list[str] | None = None) -> SandboxResult:
        """Run ``code`` once; then, if ``tests`` were given, run code + tests in a second fresh sandbox."""
        static = analyse(code)
        result = self._single(code, inputs, None, task_id, requirements)
        result.static_analysis = static
        if tests is not None:
            probe = self._single(code, inputs, tests, task_id, requirements)
            result.verification = self._parse_tests(probe)
            result.verification["run_id"] = probe.run_id
            result.verification["static_ok"] = static["ok"]
        result.verified = bool(static["ok"] and tests is not None and result.verification and result.verification["passed"])
        if self.manifest_error:
            result.error = (result.error + "; " if result.error else "") + f"vendored dependencies refused: {self.manifest_error}"
        if self.log is not None:
            try:
                rec = self.log.record({
                    "run_id": result.run_id, "task_id": task_id, "backend": result.backend, "ok": result.ok,
                    "verified": result.verified, "exit_code": result.exit_code, "limit_hit": result.limit_hit,
                    "duration_ms": result.duration_ms, "peak_rss_mb": result.peak_rss_mb, "cpu_seconds": result.cpu_seconds,
                    "stdout_sha256": _sha256_bytes(result.stdout.encode("utf-8", "replace")),
                    "stderr_sha256": _sha256_bytes(result.stderr.encode("utf-8", "replace")),
                    "files_written": [f.model_dump() for f in result.files_written],
                    "files_changed": result.files_changed, "egress_attempts": result.egress_attempts,
                    "code_sha256": _sha256_bytes(code.encode("utf-8")),
                    "tests_sha256": _sha256_bytes(tests.encode("utf-8")) if tests else None,
                    "manifest_hash": result.manifest_hash, "static_ok": static["ok"],
                    "tests": {k: result.verification[k] for k in ("tests_total", "tests_passed", "passed")} if result.verification else None,
                    "workdir_destroyed": result.workdir_destroyed, "job_object": result.job_object,
                })
                result.log_hash = rec["hash"]
            except Exception as exc:
                logger.warning("sandbox run log write failed: %s", exc)
        return result

    def describe(self) -> dict:
        return {"backend": self.backend, "python": self.python, "limits": self.limits.model_dump(),
                "manifest_hash": self.manifest_hash, "vendor": self.vendor_path, "manifest_error": self.manifest_error,
                "banned_imports": list(BANNED_IMPORTS), "egress": "sockets neutered in-process; docker backend has no network namespace",
                "resource_caps": "windows job object (process memory, CPU time, one process, kill on close)" if os.name == "nt" else "posix rlimits (AS, CPU, FSIZE, NPROC)",
                "ephemeral": "fresh working directory per run, destroyed after; artifacts returned by hash and base64"}
