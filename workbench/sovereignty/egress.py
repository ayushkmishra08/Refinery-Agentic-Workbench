"""In-process egress guard: this process cannot open a socket to anything off-premises.

Installed once at start-up (``install_egress_guard``). ``socket.socket.connect`` / ``connect_ex``
and ``socket.create_connection`` are wrapped; a destination that is not loopback, not a private
range and not on the allow-list raises ``EgressBlocked`` *before* any packet leaves, and the
attempt is written to a hash-chained log. The model libraries' offline flags are set too, so a
missing weight file fails loudly instead of quietly reaching Hugging Face.

This is belt-and-braces over the physical air gap, not a substitute for it: the monitor and the
pulled cable are the proof, this is the thing that makes an accidental ``pip install`` or a
telemetry ping fail inside the process.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
import time
from typing import Any

from workbench.sovereignty.hashchain import HashChainedLog

logger = logging.getLogger(__name__)

PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "fe80::/10", "fc00::/7", "0.0.0.0/8",
)]
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "NO_PROXY": "*", "no_proxy": "*",
}


class EgressBlocked(OSError):
    """Raised inside the process when code tries to reach a non-local address."""


class EgressGuard:
    _instance: "EgressGuard | None" = None

    def __init__(self, allowed_hosts: list[str] | None = None, log: HashChainedLog | None = None,
                 allow_private: bool = True) -> None:
        self.allowed_hosts = {h.lower() for h in (allowed_hosts or ["127.0.0.1", "::1", "localhost"])}
        self.allow_private = allow_private
        self.log = log
        self.blocked: list[dict[str, Any]] = []
        self.installed = False
        self._lock = threading.Lock()
        self._orig: dict[str, Any] = {}

    # ------------------------------------------------------------------ policy
    def permitted(self, host: str | None, port: int | None = None) -> tuple[bool, str]:
        if host is None:
            return True, "no host"
        h = str(host).strip("[]").lower()
        if h in self.allowed_hosts:
            return True, "allow-list"
        try:
            ip = ipaddress.ip_address(h)
        except ValueError:
            return False, f"hostname {h!r} is not on the allow-list (no DNS off-premises)"
        if ip.is_loopback:
            return True, "loopback"
        if self.allow_private and any(ip in n for n in PRIVATE_NETS):
            return True, "private range"
        return False, f"{h} is a public address"

    def _refuse(self, host: Any, port: Any, reason: str) -> None:
        rec = {"ts": time.time(), "host": str(host), "port": port, "reason": reason, "thread": threading.current_thread().name}
        with self._lock:
            self.blocked.append(rec)
            if len(self.blocked) > 500:
                del self.blocked[:-500]
        if self.log is not None:
            try:
                self.log.append({"event": "egress_blocked", **rec})
            except Exception:
                pass
        logger.warning("egress blocked: %s:%s (%s)", host, port, reason)
        raise EgressBlocked(f"air-gap: connection to {host}:{port} refused ({reason})")

    @staticmethod
    def _addr(address: Any) -> tuple[Any, Any]:
        if isinstance(address, (tuple, list)) and len(address) >= 2:
            return address[0], address[1]
        if isinstance(address, str):
            return address, None                       # AF_UNIX path
        return None, None

    # ------------------------------------------------------------------ install / remove
    def install(self) -> None:
        if self.installed:
            return
        guard = self
        orig_connect = socket.socket.connect
        orig_connect_ex = socket.socket.connect_ex
        orig_create = socket.create_connection

        def connect(sock, address):
            host, port = guard._addr(address)
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                ok, why = guard.permitted(host, port)
                if not ok:
                    guard._refuse(host, port, why)
            return orig_connect(sock, address)

        def connect_ex(sock, address):
            host, port = guard._addr(address)
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                ok, why = guard.permitted(host, port)
                if not ok:
                    guard._refuse(host, port, why)
            return orig_connect_ex(sock, address)

        def create_connection(address, *a, **kw):
            host, port = guard._addr(address)
            ok, why = guard.permitted(host, port)
            if not ok:
                guard._refuse(host, port, why)
            return orig_create(address, *a, **kw)

        self._orig = {"connect": orig_connect, "connect_ex": orig_connect_ex, "create_connection": orig_create}
        socket.socket.connect = connect            # type: ignore[method-assign]
        socket.socket.connect_ex = connect_ex      # type: ignore[method-assign]
        socket.create_connection = create_connection
        for k, v in OFFLINE_ENV.items():
            os.environ.setdefault(k, v)
        self.installed = True
        EgressGuard._instance = self
        logger.info("egress guard installed; allowed hosts: %s", sorted(self.allowed_hosts))

    def remove(self) -> None:
        if not self.installed:
            return
        socket.socket.connect = self._orig["connect"]                # type: ignore[method-assign]
        socket.socket.connect_ex = self._orig["connect_ex"]          # type: ignore[method-assign]
        socket.create_connection = self._orig["create_connection"]
        self.installed = False
        if EgressGuard._instance is self:
            EgressGuard._instance = None

    def status(self) -> dict:
        return {"installed": self.installed, "allowed_hosts": sorted(self.allowed_hosts), "allow_private": self.allow_private,
                "blocked_count": len(self.blocked), "recent_blocked": self.blocked[-10:],
                "offline_env": {k: os.environ.get(k) for k in OFFLINE_ENV}}

    @classmethod
    def current(cls) -> "EgressGuard | None":
        return cls._instance


def install_egress_guard(allowed_hosts: list[str] | None = None, log: HashChainedLog | None = None) -> EgressGuard:
    guard = EgressGuard.current()
    if guard is not None and guard.installed:
        if allowed_hosts:
            guard.allowed_hosts |= {h.lower() for h in allowed_hosts}
        if log is not None and guard.log is None:
            guard.log = log
        return guard
    guard = EgressGuard(allowed_hosts, log)
    guard.install()
    return guard
