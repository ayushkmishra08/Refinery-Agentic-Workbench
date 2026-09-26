"""Air-gap proof: in-process egress guard, network monitor, chained connection log."""
from __future__ import annotations

import socket
import time

import pytest

from workbench.sovereignty.egress import EgressBlocked, EgressGuard
from workbench.sovereignty.hashchain import HashChainedLog
from workbench.sovereignty.netmonitor import NetworkMonitor, classify_ip
from workbench.sovereignty.service import SovereigntyService


@pytest.fixture
def guard(tmp_path):
    g = EgressGuard(["127.0.0.1", "::1", "localhost"], HashChainedLog(tmp_path / "egress.jsonl"))
    g.install()
    yield g
    g.remove()


def test_policy():
    g = EgressGuard()
    assert g.permitted("127.0.0.1", 80)[0]
    assert g.permitted("localhost", 11434)[0]
    assert g.permitted("192.168.1.20", 7687)[0]
    assert not g.permitted("8.8.8.8", 53)[0]
    assert not g.permitted("example.com", 443)[0]


def test_public_socket_is_refused_before_connecting(guard):
    t0 = time.time()
    with pytest.raises(EgressBlocked):
        socket.create_connection(("8.8.8.8", 53), timeout=5)
    assert time.time() - t0 < 1.0                       # refused in-process, not by a timeout
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(EgressBlocked):
        s.connect(("1.1.1.1", 443))
    s.close()
    st = guard.status()
    assert st["blocked_count"] == 2 and st["installed"]
    assert guard.log.verify().ok and len(guard.log) == 2
    assert guard.log.read()[0]["host"] == "8.8.8.8"


def test_loopback_still_allowed(guard):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    c = socket.create_connection(("127.0.0.1", port), timeout=2)
    c.close()
    srv.close()
    assert guard.status()["blocked_count"] == 0


def test_remove_restores_socket(guard):
    guard.remove()
    assert socket.create_connection.__name__ == "create_connection"
    assert not guard.installed


def test_classify_ip():
    assert classify_ip("127.0.0.1") == "loopback"
    assert classify_ip("10.1.2.3") == "private"
    assert classify_ip("192.168.0.9") == "private"
    assert classify_ip("34.149.66.165") == "external"
    assert classify_ip("::1") == "loopback"
    assert classify_ip("") == "none"


def test_monitor_samples_and_chains(tmp_path):
    mon = NetworkMonitor(tmp_path, interval=0.2)
    mon.start()
    time.sleep(0.7)
    st = mon.status()
    mon.stop()
    assert st["samples"] >= 1 and st["chain"]["ok"] and st["log_entries"] >= 1
    rows = mon.log.read()
    assert rows[0]["event"] == "monitor_started"
    assert mon.log.verify().ok
    assert "verdict" in st and isinstance(st["interfaces"], list)


def test_service_report(tmp_path, cfg):
    c = cfg.model_copy(deep=True)
    c.sovereignty.sovereignty_dir = tmp_path / "sov"
    c.sovereignty.monitor_interval_seconds = 0.2
    c.sovereignty.airgap_enforced = True
    c.paths.audit_dir = tmp_path / "audit"
    c.paths.security_dir = tmp_path / "security"
    svc = SovereigntyService(c)
    try:
        time.sleep(0.5)
        rep = svc.report()
        assert rep["airgap_enforced"] and rep["egress_guard"]["installed"]
        assert rep["network_monitor"]["running"]
        assert rep["chains_intact"]
        assert any(r["log"] == "connection log" and r["exists"] for r in rep["chains"])
        assert "egress guard installed" in rep["verdict"]
    finally:
        svc.shutdown()
        if svc.guard:
            svc.guard.remove()
