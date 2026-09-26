from __future__ import annotations

import ssl
from pathlib import Path

from workbench.security.tls import describe, ensure_certificates, httpx_client_kwargs, uvicorn_ssl_kwargs


def test_certificates_created_and_idempotent(tmp_path):
    m1 = ensure_certificates(tmp_path / "tls")
    assert m1.created
    for p in (m1.ca_cert, m1.ca_key, m1.server_cert, m1.server_key, m1.client_cert, m1.client_key, m1.client_bundle):
        assert Path(p).exists()
    m2 = ensure_certificates(tmp_path / "tls")
    assert not m2.created
    assert (m1.ca_fingerprint, m1.server_fingerprint, m1.client_fingerprint) == (m2.ca_fingerprint, m2.server_fingerprint, m2.client_fingerprint)
    assert m1.ca_fingerprint != m1.server_fingerprint
    assert describe(m1)["hostnames"] == ["localhost", "127.0.0.1"]


def test_ssl_kwargs(tmp_path):
    m = ensure_certificates(tmp_path / "tls")
    plain = uvicorn_ssl_kwargs(m, mtls=False)
    assert set(plain) == {"ssl_certfile", "ssl_keyfile"}
    mtls = uvicorn_ssl_kwargs(m, mtls=True)
    assert mtls["ssl_ca_certs"] == m.ca_cert and mtls["ssl_cert_reqs"] == ssl.CERT_REQUIRED
    kw = httpx_client_kwargs(m, mtls=True)
    assert isinstance(kw["verify"], ssl.SSLContext) and kw["client_cert"] == (m.client_cert, m.client_key)
    assert "client_cert" not in httpx_client_kwargs(m, mtls=False)
    assert isinstance(httpx_client_kwargs(m, mtls=False)["verify"], ssl.SSLContext)
