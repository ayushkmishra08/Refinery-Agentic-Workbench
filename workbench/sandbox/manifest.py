"""What the sandbox may import, pinned and checksum-verified.

The sandbox interpreter runs in isolated mode (``-I -S``): no site-packages, no user site, no
``PYTHONPATH``. The only third-party code it can reach is the vendored directory
``workbench/sandbox/vendor/`` — and only when every file in it still matches ``manifest.json``.
A wheel or a source tree is added to that directory at *build* time, its checksum recorded,
and from then on nothing is fetched while a task runs: ``pip install`` inside a task cannot
reach the network (sockets are neutered) and would not be on the path if it did.

    python -c "from workbench.sandbox.manifest import build_manifest; build_manifest()"

after dropping a package into the vendor directory re-pins it. ``verify_manifest`` refuses a
directory whose contents drifted, and the runner then falls back to the standard library only
and says so in the result.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from pydantic import BaseModel, Field

VENDOR_DIR = Path(__file__).parent / "vendor"
MANIFEST_NAME = "manifest.json"

#: Standard-library modules a task may import. ``os`` is here because ``os.path`` and
#: ``os.getcwd`` are needed by ordinary code; the dangerous ``os`` functions are stubbed to raise
#: by the preamble. ``socket`` is here because the preamble replaces its constructors.
ALLOWED_STDLIB: frozenset[str] = frozenset({
    "abc", "array", "ast", "base64", "binascii", "bisect", "builtins", "calendar", "cmath", "codecs", "collections",
    "colorsys", "contextlib", "copy", "csv", "dataclasses", "datetime", "decimal", "difflib", "enum", "errno", "fractions",
    "functools", "gc", "getopt", "glob", "graphlib", "hashlib", "heapq", "hmac", "html", "io", "itertools", "json", "keyword",
    "locale", "logging", "math", "numbers", "operator", "os", "pathlib", "pprint", "queue", "random", "re", "reprlib", "secrets",
    "shlex", "statistics", "string", "struct", "sys", "tempfile", "textwrap", "time", "timeit", "tokenize", "traceback", "types",
    "typing", "unicodedata", "unittest", "uuid", "warnings", "weakref", "zlib", "zoneinfo", "socket", "shutil", "stat", "fnmatch",
    "posixpath", "ntpath", "genericpath", "linecache", "inspect", "dis", "opcode", "importlib", "encodings", "codeop", "atexit",
    "threading", "concurrent", "_thread", "sre_compile", "sre_parse", "sre_constants", "copyreg", "select", "selectors",
})

#: Modules the import hook refuses outright (top-level name or dotted prefix).
BANNED_IMPORTS: tuple[str, ...] = (
    "subprocess", "ctypes", "multiprocessing", "requests", "httpx", "urllib3", "paramiko", "ftplib", "smtplib",
    "telnetlib", "xmlrpc", "webbrowser", "pty", "signal", "ssl", "urllib.request", "http.client", "http.server",
    "socketserver", "asyncio", "pip", "setuptools", "distutils", "ensurepip", "venv", "sysconfig", "site", "runpy",
    "code", "pdb", "cProfile", "resource", "mmap", "msvcrt", "winreg", "_posixsubprocess", "pwd", "grp",
)


class ManifestError(RuntimeError):
    """The vendor directory does not match its manifest: refuse it rather than trust it."""


class VendoredFile(BaseModel):
    path: str
    sha256: str
    bytes: int


class SandboxManifest(BaseModel):
    version: int = 1
    python: str = Field(default_factory=lambda: sys.version.split()[0])
    stdlib: list[str] = Field(default_factory=lambda: sorted(ALLOWED_STDLIB))
    banned: list[str] = Field(default_factory=lambda: list(BANNED_IMPORTS))
    vendor: list[VendoredFile] = Field(default_factory=list)

    @property
    def hash(self) -> str:
        body = json.dumps({"stdlib": self.stdlib, "banned": self.banned,
                           "vendor": [v.model_dump() for v in self.vendor]}, sort_keys=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _vendor_files(vendor_dir: Path) -> list[VendoredFile]:
    out: list[VendoredFile] = []
    for p in sorted(vendor_dir.rglob("*")):
        if p.is_file() and p.name != MANIFEST_NAME and "__pycache__" not in p.parts:
            out.append(VendoredFile(path=p.relative_to(vendor_dir).as_posix(), sha256=_sha256(p), bytes=p.stat().st_size))
    return out


def build_manifest(vendor_dir: Path | None = None) -> SandboxManifest:
    """Pin the vendor directory as it is now. Writes ``manifest.json`` inside it."""
    vendor_dir = Path(vendor_dir or VENDOR_DIR)
    vendor_dir.mkdir(parents=True, exist_ok=True)
    manifest = SandboxManifest(vendor=_vendor_files(vendor_dir))
    (vendor_dir / MANIFEST_NAME).write_text(json.dumps(manifest.model_dump(), indent=2), encoding="utf-8")
    return manifest


def verify_manifest(vendor_dir: Path | None = None) -> SandboxManifest:
    """The manifest for a vendor directory whose every file still matches it.

    Raises ``ManifestError`` when the manifest is missing, a pinned file is missing or altered, or
    an unpinned file has appeared. A vendor directory that does not exist at all is a stdlib-only
    sandbox and returns an empty manifest.
    """
    vendor_dir = Path(vendor_dir or VENDOR_DIR)
    if not vendor_dir.exists():
        return SandboxManifest()
    mpath = vendor_dir / MANIFEST_NAME
    if not mpath.exists():
        raise ManifestError(f"{vendor_dir} has files but no {MANIFEST_NAME}; run build_manifest() to pin it")
    manifest = SandboxManifest.model_validate(json.loads(mpath.read_text(encoding="utf-8")))
    pinned = {v.path: v for v in manifest.vendor}
    actual = {v.path: v for v in _vendor_files(vendor_dir)}
    for path, v in pinned.items():
        cur = actual.get(path)
        if cur is None:
            raise ManifestError(f"pinned file missing from vendor directory: {path}")
        if cur.sha256 != v.sha256:
            raise ManifestError(f"checksum mismatch for vendored file {path}")
    extra = sorted(set(actual) - set(pinned))
    if extra:
        raise ManifestError(f"unpinned files in vendor directory: {', '.join(extra[:5])}")
    return manifest


def manifest_hash(vendor_dir: Path | None = None) -> str:
    try:
        return verify_manifest(vendor_dir).hash
    except ManifestError:
        return "invalid"
