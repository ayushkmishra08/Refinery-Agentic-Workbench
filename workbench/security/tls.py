"""Local TLS / mTLS for the internal API.

Even on one machine, traffic between the browser (or another local component) and the workbench
should not cross a socket in plaintext: a process that can read loopback traffic would otherwise
read every released answer. ``ensure_certificates`` mints a local CA, a server certificate and a
client certificate under ``<security_dir>/tls/``; ``uvicorn_ssl_kwargs`` turns them into the
arguments ``uvicorn.run`` needs, with ``mtls=True`` requiring the client certificate as well.

Nothing here contacts a certificate authority or the network: the CA is the deployment's own.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pydantic import BaseModel


class TlsMaterial(BaseModel):
    dir: str
    ca_cert: str
    ca_key: str
    server_cert: str
    server_key: str
    client_cert: str
    client_key: str
    client_bundle: str
    ca_fingerprint: str
    server_fingerprint: str
    client_fingerprint: str
    expires: str
    hostnames: list[str]
    created: bool = False


def _fingerprint(cert: x509.Certificate) -> str:
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def _write_key(path: Path, key) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _load_cert(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def _name(common: str) -> x509.Name:
    return x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Refinery Engineering AI Workbench"),
        x509.NameAttribute(NameOID.COMMON_NAME, common),
    ])


def _san(hostnames) -> x509.SubjectAlternativeName:
    names = []
    for h in hostnames:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            names.append(x509.DNSName(h))
    return x509.SubjectAlternativeName(names)


def ensure_certificates(tls_dir: Path, *, hostnames=("localhost", "127.0.0.1"), days: int = 825) -> TlsMaterial:
    """Create (once) a local CA plus server and client certificates; reuse them while valid."""
    tls_dir = Path(tls_dir)
    tls_dir.mkdir(parents=True, exist_ok=True)
    paths = {k: tls_dir / f for k, f in (("ca_key", "ca.key"), ("ca_cert", "ca.crt"), ("server_key", "server.key"),
                                          ("server_cert", "server.crt"), ("client_key", "client.key"),
                                          ("client_cert", "client.crt"), ("client_bundle", "client.pem"))}
    hostnames = list(hostnames)
    now = dt.datetime.now(dt.timezone.utc)
    created = False

    def valid(path: Path) -> bool:
        if not path.exists():
            return False
        try:
            return _load_cert(path).not_valid_after_utc > now + dt.timedelta(days=1)
        except Exception:
            return False

    if not all(p.exists() for p in paths.values()) or not all(valid(paths[k]) for k in ("ca_cert", "server_cert", "client_cert")):
        created = True
        ca_key = ec.generate_private_key(ec.SECP256R1())
        ca_cert = (x509.CertificateBuilder()
                   .subject_name(_name("Workbench Local CA")).issuer_name(_name("Workbench Local CA"))
                   .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=days))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                   .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                                content_commitment=False, key_encipherment=False, data_encipherment=False,
                                                key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
                   .sign(ca_key, hashes.SHA256()))
        ca_ski = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value

        def leaf(common: str, usage, san: bool):
            key = ec.generate_private_key(ec.SECP256R1())
            b = (x509.CertificateBuilder()
                 .subject_name(_name(common)).issuer_name(ca_cert.subject)
                 .public_key(key.public_key()).serial_number(x509.random_serial_number())
                 .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=days))
                 .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                 .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                              data_encipherment=False, key_agreement=True, key_cert_sign=False, crl_sign=False,
                                              encipher_only=False, decipher_only=False), critical=True)
                 .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
                 .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                 # the chain-building extension OpenSSL insists on: "Missing Authority Key Identifier" without it
                 .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski), critical=False))
            if san:
                b = b.add_extension(_san(hostnames), critical=False)
            return key, b.sign(ca_key, hashes.SHA256())

        server_key, server_cert = leaf(hostnames[0], ExtendedKeyUsageOID.SERVER_AUTH, san=True)
        client_key, client_cert = leaf("workbench-client", ExtendedKeyUsageOID.CLIENT_AUTH, san=False)
        _write_key(paths["ca_key"], ca_key)
        _write_cert(paths["ca_cert"], ca_cert)
        _write_key(paths["server_key"], server_key)
        _write_cert(paths["server_cert"], server_cert)
        _write_key(paths["client_key"], client_key)
        _write_cert(paths["client_cert"], client_cert)
        paths["client_bundle"].write_bytes(paths["client_cert"].read_bytes() + paths["client_key"].read_bytes())
        try:
            paths["client_bundle"].chmod(0o600)
        except OSError:
            pass

    ca_cert = _load_cert(paths["ca_cert"])
    server_cert = _load_cert(paths["server_cert"])
    client_cert = _load_cert(paths["client_cert"])
    return TlsMaterial(
        dir=str(tls_dir), ca_cert=str(paths["ca_cert"]), ca_key=str(paths["ca_key"]),
        server_cert=str(paths["server_cert"]), server_key=str(paths["server_key"]),
        client_cert=str(paths["client_cert"]), client_key=str(paths["client_key"]), client_bundle=str(paths["client_bundle"]),
        ca_fingerprint=_fingerprint(ca_cert), server_fingerprint=_fingerprint(server_cert),
        client_fingerprint=_fingerprint(client_cert), expires=server_cert.not_valid_after_utc.isoformat(),
        hostnames=hostnames, created=created,
    )


def uvicorn_ssl_kwargs(material: TlsMaterial, mtls: bool = False) -> dict:
    out = {"ssl_certfile": material.server_cert, "ssl_keyfile": material.server_key}
    if mtls:
        out["ssl_ca_certs"] = material.ca_cert
        out["ssl_cert_reqs"] = ssl.CERT_REQUIRED
    return out


def client_ssl_context(material: TlsMaterial, mtls: bool = False):
    """An SSL context that trusts only the local CA and, for mTLS, presents the client certificate."""
    import ssl

    ctx = ssl.create_default_context(cafile=material.ca_cert)
    if mtls:
        ctx.load_cert_chain(material.client_cert, material.client_key)
    return ctx


def httpx_client_kwargs(material: TlsMaterial, mtls: bool = False) -> dict:
    """Keyword arguments for ``httpx.Client``: ``verify`` is a context (httpx 0.28 dropped ``cert=``)."""
    out: dict = {"verify": client_ssl_context(material, mtls)}
    if mtls:
        out["client_cert"] = (material.client_cert, material.client_key)     # informational; the context carries it
    return out


def describe(material: TlsMaterial) -> dict:
    return {
        "dir": material.dir, "hostnames": material.hostnames, "expires": material.expires,
        "ca_fingerprint_sha256": material.ca_fingerprint, "server_fingerprint_sha256": material.server_fingerprint,
        "client_fingerprint_sha256": material.client_fingerprint,
        "files": {"ca_cert": material.ca_cert, "server_cert": material.server_cert, "client_bundle": material.client_bundle},
        "note": "Self-issued local CA; trust ca.crt in the browser/client. mTLS requires client.pem.",
    }
