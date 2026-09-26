"""Signed, checksum-verified model packages. Models arrive on media, never over the wire.

A package is a directory (or ``.tar``) holding an Ollama ``Modelfile``, the weight blobs it
references (``FROM ./model.gguf``), a ``MANIFEST.json`` naming every file with its SHA-256, and a
``MANIFEST.sig`` — an Ed25519 signature over the canonical manifest. ``import_package`` refuses
to hand anything to Ollama until every hash matches and the signature verifies against a key in
the local trust store. That is the supply-chain check: a package altered in transit, or signed by
someone the deployment does not trust, is caught before a model loads.

Both outcomes are written to a hash-chained log so a reviewer can see what was loaded, when,
and under whose key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, Field

from workbench.sovereignty.hashchain import HashChainedLog, canonical

logger = logging.getLogger(__name__)
MANIFEST = "MANIFEST.json"
SIGNATURE = "MANIFEST.sig"


class VerificationReport(BaseModel):
    ok: bool
    name: str = ""
    version: str = ""
    key_id: str = ""
    files_checked: int = 0
    mismatches: list[str] = Field(default_factory=list)
    signature_ok: bool = False
    signer_trusted: bool = False
    detail: str = ""
    package: str = ""


# --------------------------------------------------------------------------- keys
class SignerKeys(BaseModel):
    key_id: str
    private_key: str
    public_key: str


def create_signer(keys_dir: Path, key_id: str) -> SignerKeys:
    """Generate an Ed25519 signing keypair; the private half is mode 600."""
    keys_dir = Path(keys_dir)
    keys_dir.mkdir(parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    priv_path = keys_dir / f"{key_id}.key"
    pub_path = keys_dir / f"{key_id}.pub"
    priv_path.write_bytes(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
    try:
        priv_path.chmod(0o600)
    except OSError:
        pass
    pub_path.write_bytes(priv.public_key().public_bytes(serialization.Encoding.PEM,
                                                        serialization.PublicFormat.SubjectPublicKeyInfo))
    return SignerKeys(key_id=key_id, private_key=str(priv_path), public_key=str(pub_path))


def trust_signer(trust_dir: Path, public_key_path: Path, key_id: str) -> Path:
    """Copy a public key into the trust store. Only trusted keys can vouch for a package."""
    trust_dir = Path(trust_dir)
    trust_dir.mkdir(parents=True, exist_ok=True)
    dest = trust_dir / f"{key_id}.pub"
    shutil.copyfile(public_key_path, dest)
    return dest


def trusted_signers(trust_dir: Path) -> list[str]:
    trust_dir = Path(trust_dir)
    return sorted(p.stem for p in trust_dir.glob("*.pub")) if trust_dir.exists() else []


# --------------------------------------------------------------------------- hashing
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _package_files(package_dir: Path) -> list[Path]:
    return sorted(p for p in package_dir.rglob("*") if p.is_file() and p.name not in (MANIFEST, SIGNATURE))


# --------------------------------------------------------------------------- sign
def sign_package(package_dir: Path, private_key_path: Path, key_id: str, name: str, version: str) -> Path:
    package_dir = Path(package_dir)
    if not (package_dir / "Modelfile").exists():
        raise FileNotFoundError("a model package needs a Modelfile")
    manifest = {
        "name": name, "version": version, "created": time.time(),
        "files": [{"path": p.relative_to(package_dir).as_posix(), "sha256": _sha256(p), "bytes": p.stat().st_size}
                  for p in _package_files(package_dir)],
        "signer": {"key_id": key_id, "algorithm": "ed25519"},
    }
    priv = serialization.load_pem_private_key(Path(private_key_path).read_bytes(), password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise ValueError("signing key must be Ed25519")
    sig = priv.sign(canonical(manifest).encode("utf-8"))
    (package_dir / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    (package_dir / SIGNATURE).write_text(base64.b64encode(sig).decode("ascii"), encoding="utf-8")
    return package_dir / MANIFEST


# --------------------------------------------------------------------------- verify
def _materialise(package: Path) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    package = Path(package)
    if package.is_dir():
        return package, None
    if package.suffix in (".tar", ".tgz") or package.name.endswith(".tar.gz"):
        tmp = tempfile.TemporaryDirectory(prefix="rwb-model-")
        with tarfile.open(package) as tf:
            tf.extractall(tmp.name, filter="data")
        root = Path(tmp.name)
        entries = [p for p in root.iterdir()]
        if len(entries) == 1 and entries[0].is_dir():
            root = entries[0]
        return root, tmp
    raise ValueError(f"{package} is neither a directory nor a tar archive")


def verify_package(package: Path, trust_dir: Path) -> VerificationReport:
    root, tmp = _materialise(package)
    try:
        report = VerificationReport(ok=False, package=str(package))
        manifest_path, sig_path = root / MANIFEST, root / SIGNATURE
        if not manifest_path.exists() or not sig_path.exists():
            report.detail = "MANIFEST.json or MANIFEST.sig missing"
            return report
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.detail = f"manifest unreadable: {exc}"
            return report
        report.name = str(manifest.get("name", ""))
        report.version = str(manifest.get("version", ""))
        report.key_id = str(manifest.get("signer", {}).get("key_id", ""))

        # 1. every listed file hashes as declared, and no file is missing or unlisted
        listed = {f["path"]: f for f in manifest.get("files", [])}
        present = {p.relative_to(root).as_posix(): p for p in _package_files(root)}
        for rel, entry in listed.items():
            p = present.get(rel)
            if p is None:
                report.mismatches.append(f"{rel}: missing")
                continue
            if _sha256(p) != entry.get("sha256"):
                report.mismatches.append(f"{rel}: sha256 mismatch")
            elif p.stat().st_size != int(entry.get("bytes", -1)):
                report.mismatches.append(f"{rel}: size mismatch")
        for rel in present:
            if rel not in listed:
                report.mismatches.append(f"{rel}: not in manifest")
        report.files_checked = len(listed)
        if "Modelfile" not in listed:
            report.mismatches.append("Modelfile: not in manifest")

        # 2. the signature verifies under a key the deployment trusts
        pub_path = Path(trust_dir) / f"{report.key_id}.pub" if report.key_id else None
        report.signer_trusted = bool(pub_path and pub_path.exists())
        if report.signer_trusted:
            try:
                pub = serialization.load_pem_public_key(pub_path.read_bytes())
                if not isinstance(pub, Ed25519PublicKey):
                    raise ValueError("trusted key is not Ed25519")
                sig = base64.b64decode(sig_path.read_text(encoding="utf-8").strip())
                pub.verify(sig, canonical(manifest).encode("utf-8"))
                report.signature_ok = True
            except (InvalidSignature, ValueError) as exc:
                report.signature_ok = False
                report.detail = f"signature invalid: {exc}"
        else:
            report.detail = f"signer {report.key_id or '?'} is not in the trust store"

        report.ok = report.signature_ok and report.signer_trusted and not report.mismatches
        if report.ok:
            report.detail = f"{report.files_checked} file(s) match; signed by trusted key {report.key_id}"
        elif report.mismatches and not report.detail:
            report.detail = f"{len(report.mismatches)} file(s) failed checksum verification"
        return report
    finally:
        if tmp is not None:
            tmp.cleanup()


# --------------------------------------------------------------------------- import
def import_package(package: Path, trust_dir: Path, *, ollama_url: str = "http://localhost:11434",
                   dry_run: bool = False, log_path: Path | None = None) -> dict:
    """Verify a package and, unless ``dry_run``, register it with the local Ollama.

    ``ollama create`` reads the Modelfile and the local blob it names; nothing is downloaded.
    """
    report = verify_package(package, trust_dir)
    log = HashChainedLog(log_path, name="model_updates") if log_path else None
    out = {"verification": report.model_dump(), "imported": False, "dry_run": dry_run, "model": None}
    if not report.ok:
        if log is not None:
            log.append({"event": "model_package_rejected", "package": str(package), "name": report.name,
                        "version": report.version, "detail": report.detail, "mismatches": report.mismatches})
        logger.warning("model package %s rejected: %s", package, report.detail)
        return out
    if log is not None:
        log.append({"event": "model_package_verified", "package": str(package), "name": report.name,
                    "version": report.version, "key_id": report.key_id, "files": report.files_checked})
    if dry_run:
        return out
    root, tmp = _materialise(package)
    try:
        tag = f"{report.name}:{report.version}" if report.version else report.name
        env = {"OLLAMA_HOST": ollama_url}
        proc = subprocess.run(["ollama", "create", tag, "-f", "Modelfile"], cwd=str(root), capture_output=True,
                              text=True, timeout=3600, env={**__import__("os").environ, **env})
        out["model"] = tag
        out["imported"] = proc.returncode == 0
        out["stdout"] = proc.stdout[-2000:]
        out["stderr"] = proc.stderr[-2000:]
        if log is not None:
            log.append({"event": "model_imported" if out["imported"] else "model_import_failed", "model": tag,
                        "returncode": proc.returncode})
    except FileNotFoundError:
        out["error"] = "ollama executable not found on PATH"
    finally:
        if tmp is not None:
            tmp.cleanup()
    return out


def update_log(log_path: Path, limit: int = 50) -> dict:
    log = HashChainedLog(log_path, name="model_updates")
    return {"entries": log.read(limit=limit), "chain": log.verify().model_dump()}
