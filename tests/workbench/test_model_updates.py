"""Signed model packages: sign, verify, detect tampering, refuse untrusted signers."""
from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from workbench.sovereignty.model_updates import (
    create_signer, import_package, sign_package, trust_signer, trusted_signers, update_log, verify_package,
)


@pytest.fixture
def package(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "tiny.gguf").write_bytes(b"GGUF\x00fake-weights")
    (pkg / "Modelfile").write_text("FROM ./tiny.gguf\nPARAMETER temperature 0.1\n", encoding="utf-8")
    return pkg


@pytest.fixture
def signer(tmp_path):
    return create_signer(tmp_path / "keys", "vendor-2026")


def test_sign_and_verify(package, signer, tmp_path):
    trust = tmp_path / "trust"
    sign_package(package, Path(signer.private_key), signer.key_id, "tinymodel", "1.0")
    assert (package / "MANIFEST.json").exists() and (package / "MANIFEST.sig").exists()
    trust_signer(trust, Path(signer.public_key), signer.key_id)
    assert trusted_signers(trust) == ["vendor-2026"]
    r = verify_package(package, trust)
    assert r.ok and r.signature_ok and r.signer_trusted and r.files_checked == 2 and r.mismatches == []
    assert r.name == "tinymodel" and r.version == "1.0"


def test_tampered_file_detected(package, signer, tmp_path):
    trust = tmp_path / "trust"
    sign_package(package, Path(signer.private_key), signer.key_id, "tinymodel", "1.0")
    trust_signer(trust, Path(signer.public_key), signer.key_id)
    raw = bytearray((package / "tiny.gguf").read_bytes())
    raw[5] ^= 0x01
    (package / "tiny.gguf").write_bytes(bytes(raw))
    r = verify_package(package, trust)
    assert not r.ok and r.signature_ok and any(m.startswith("tiny.gguf") for m in r.mismatches)
    # an extra, unlisted file is also a mismatch
    (package / "tiny.gguf").write_bytes(bytes(raw[:5] + bytes([raw[5] ^ 0x01]) + raw[6:]))
    (package / "extra.bin").write_bytes(b"x")
    r = verify_package(package, trust)
    assert not r.ok and "extra.bin: not in manifest" in r.mismatches


def test_untrusted_signer_rejected(package, signer, tmp_path):
    sign_package(package, Path(signer.private_key), signer.key_id, "tinymodel", "1.0")
    r = verify_package(package, tmp_path / "empty-trust")
    assert not r.ok and not r.signer_trusted and not r.signature_ok


def test_tampered_manifest_breaks_signature(package, signer, tmp_path):
    trust = tmp_path / "trust"
    sign_package(package, Path(signer.private_key), signer.key_id, "tinymodel", "1.0")
    trust_signer(trust, Path(signer.public_key), signer.key_id)
    m = package / "MANIFEST.json"
    m.write_text(m.read_text(encoding="utf-8").replace('"1.0"', '"9.9"'), encoding="utf-8")
    r = verify_package(package, trust)
    assert not r.ok and r.signer_trusted and not r.signature_ok


def test_tar_package_and_dry_run_import(package, signer, tmp_path):
    trust = tmp_path / "trust"
    sign_package(package, Path(signer.private_key), signer.key_id, "tinymodel", "1.0")
    trust_signer(trust, Path(signer.public_key), signer.key_id)
    tar = tmp_path / "tinymodel.tar"
    with tarfile.open(tar, "w") as tf:
        tf.add(package, arcname="pkg")
    log = tmp_path / "model_updates.jsonl"
    out = import_package(tar, trust, dry_run=True, log_path=log)
    assert out["verification"]["ok"] and out["dry_run"] and not out["imported"] and out["model"] is None
    entries = update_log(log)
    assert entries["chain"]["ok"] and entries["entries"][-1]["event"] == "model_package_verified"
    bad = import_package(tar, tmp_path / "no-trust", dry_run=True, log_path=log)
    assert not bad["verification"]["ok"] and update_log(log)["entries"][-1]["event"] == "model_package_rejected"
