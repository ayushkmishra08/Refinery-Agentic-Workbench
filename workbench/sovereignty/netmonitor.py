"""Live network monitor with an immutable connection log.

A background thread samples the host's socket table, interface state and traffic counters every
few seconds for the whole session and writes to a hash-chained log:

- every *new* connection tuple (pid, process, local, remote, status), classified as loopback /
  private / **external**;
- every interface going up or down — a pulled uplink cable shows up as an ``interface_down``
  entry with a timestamp, which is the point of pulling it in front of a judge;
- a periodic traffic summary per interface (bytes in/out since the last sample).

``status()`` answers the demonstrable question: *has any external connection been observed
since the monitor started?* — with the count, the last few, the chain verification, and the
interfaces' current state. Nothing here decides anything; it records.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from workbench.sovereignty.egress import PRIVATE_NETS
from workbench.sovereignty.hashchain import HashChainedLog

logger = logging.getLogger(__name__)


def classify_ip(host: str | None) -> str:
    if not host:
        return "none"
    try:
        ip = ipaddress.ip_address(str(host).strip("[]"))
    except ValueError:
        return "unresolved"
    if ip.is_loopback:
        return "loopback"
    if ip.is_unspecified:
        return "unspecified"
    if any(ip in n for n in PRIVATE_NETS) or ip.is_private or ip.is_link_local:
        return "private"
    return "external"


class NetworkMonitor:
    def __init__(self, log_dir: Path, *, interval: float = 2.0, scope: str = "host",
                 summary_every: int = 15, watch_names: tuple[str, ...] = ("python", "ollama", "uvicorn", "node")) -> None:
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = HashChainedLog(self.dir / "connections.jsonl", name="connections")
        self.interval = interval
        self.scope = scope
        self.summary_every = summary_every
        self.watch_names = tuple(n.lower() for n in watch_names)
        self.started: float | None = None
        self.samples = 0
        self.seen: set[tuple] = set()
        self.external: list[dict[str, Any]] = []
        self.own_external: list[dict[str, Any]] = []     # external connections by the workbench / Ollama themselves
        self.connections_seen = 0
        self.if_state: dict[str, bool] = {}
        self.if_events: list[dict[str, Any]] = []
        self._io_prev: dict[str, tuple[int, int]] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pids: set[int] = set()
        self.errors = 0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "NetworkMonitor":
        if self._thread and self._thread.is_alive():
            return self
        self.started = time.time()
        self._stop.clear()
        self.log.append({"event": "monitor_started", "scope": self.scope, "interval": self.interval,
                         "host": os.environ.get("COMPUTERNAME") or os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", ""),
                         "pid": os.getpid()})
        self._snapshot_interfaces(initial=True)
        self._thread = threading.Thread(target=self._loop, name="netmonitor", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 1)
        try:
            self.log.append({"event": "monitor_stopped", "samples": self.samples,
                             "external_connections": len(self.external)})
        except Exception:
            pass

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------ sampling
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample()
            except Exception as exc:        # never die: the log must cover the whole session
                self.errors += 1
                logger.debug("netmonitor sample failed: %s", exc)
            self._stop.wait(self.interval)

    def own_pids(self) -> set[int]:
        """This process, its children, and the local model server — the processes that matter most."""
        pids = {os.getpid()}
        try:
            me = psutil.Process(os.getpid())
            pids |= {c.pid for c in me.children(recursive=True)}
        except Exception:
            pass
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if (p.info["name"] or "").lower().startswith(self.watch_names):
                    pids.add(p.info["pid"])
            except Exception:
                continue
        return pids

    def _process_scope(self) -> set[int]:
        return self.own_pids() if self.scope == "process" else set()

    def sample(self) -> None:
        self.samples += 1
        pids = self._process_scope()
        own = self.own_pids()
        try:
            conns = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            conns = []
            for p in psutil.process_iter(["pid"]):
                try:
                    conns.extend(p.net_connections(kind="inet") if hasattr(p, "net_connections") else p.connections(kind="inet"))
                except Exception:
                    continue
        for c in conns:
            if pids and c.pid not in pids:
                continue
            if not c.raddr:
                continue                                    # listening / unconnected sockets carry no destination
            r_ip, r_port = (c.raddr[0], c.raddr[1]) if isinstance(c.raddr, tuple) else (getattr(c.raddr, "ip", ""), getattr(c.raddr, "port", 0))
            l_ip, l_port = (c.laddr[0], c.laddr[1]) if isinstance(c.laddr, tuple) else (getattr(c.laddr, "ip", ""), getattr(c.laddr, "port", 0))
            key = (c.pid, l_ip, l_port, r_ip, r_port, c.status)
            if key in self.seen:
                continue
            self.seen.add(key)
            if len(self.seen) > 20000:
                self.seen = set(list(self.seen)[-10000:])
            klass = classify_ip(r_ip)
            name = ""
            try:
                name = psutil.Process(c.pid).name() if c.pid else ""
            except Exception:
                pass
            mine = c.pid in own
            rec = {"event": "connection", "pid": c.pid, "process": name, "laddr": f"{l_ip}:{l_port}",
                   "raddr": f"{r_ip}:{r_port}", "status": c.status, "class": klass, "workbench_process": mine}
            self.connections_seen += 1
            self.log.append(rec)
            if klass == "external":
                rec = {**rec, "ts": time.time()}
                with self._lock:
                    self.external.append(rec)
                    if len(self.external) > 500:
                        del self.external[:-500]
                    if mine:
                        self.own_external.append(rec)
                        if len(self.own_external) > 500:
                            del self.own_external[:-500]
                if mine:
                    logger.warning("EXTERNAL connection by a workbench process: %s %s -> %s (%s)", name, rec["laddr"], rec["raddr"], c.status)
                else:
                    logger.info("external connection on host: %s %s -> %s (%s)", name, rec["laddr"], rec["raddr"], c.status)
        self._snapshot_interfaces()
        if self.samples % self.summary_every == 0:
            self._traffic_summary()

    def _snapshot_interfaces(self, initial: bool = False) -> None:
        try:
            stats = psutil.net_if_stats()
        except Exception:
            return
        for name, st in stats.items():
            up = bool(st.isup)
            prev = self.if_state.get(name)
            if prev is None:
                self.if_state[name] = up
                if initial:
                    self.log.append({"event": "interface", "name": name, "isup": up, "speed_mbps": st.speed, "mtu": st.mtu})
                continue
            if prev != up:
                self.if_state[name] = up
                ev = {"event": "interface_up" if up else "interface_down", "name": name, "speed_mbps": st.speed, "ts": time.time()}
                with self._lock:
                    self.if_events.append(ev)
                    if len(self.if_events) > 100:
                        del self.if_events[:-100]
                self.log.append(ev)
                logger.warning("interface %s went %s", name, "UP" if up else "DOWN (uplink pulled?)")

    def _traffic_summary(self) -> None:
        try:
            io = psutil.net_io_counters(pernic=True)
        except Exception:
            return
        deltas = {}
        for name, ctr in io.items():
            prev = self._io_prev.get(name)
            self._io_prev[name] = (ctr.bytes_sent, ctr.bytes_recv)
            if prev is not None:
                deltas[name] = {"sent": ctr.bytes_sent - prev[0], "recv": ctr.bytes_recv - prev[1]}
        if deltas:
            self.log.append({"event": "traffic", "bytes": deltas, "sample": self.samples})

    # ------------------------------------------------------------------ reporting
    def interfaces(self) -> list[dict]:
        out = []
        try:
            stats = psutil.net_if_stats()
            io = psutil.net_io_counters(pernic=True)
            addrs = psutil.net_if_addrs()
        except Exception:
            return out
        for name, st in stats.items():
            ctr = io.get(name)
            ips = [a.address for a in addrs.get(name, []) if a.family in (2, 23)]  # AF_INET / AF_INET6
            out.append({"name": name, "isup": bool(st.isup), "speed_mbps": st.speed, "mtu": st.mtu,
                        "addresses": ips[:4],
                        "bytes_sent": ctr.bytes_sent if ctr else 0, "bytes_recv": ctr.bytes_recv if ctr else 0,
                        "loopback": any(classify_ip(ip) == "loopback" for ip in ips)})
        return sorted(out, key=lambda r: (not r["isup"], r["name"]))

    def status(self) -> dict:
        with self._lock:
            ext = list(self.external[-10:])
            own_ext = list(self.own_external[-10:])
            if_events = list(self.if_events[-10:])
        chain = self.log.verify()
        uplinks = [i for i in self.interfaces() if not i["loopback"]]
        if not self.external:
            verdict = "no external connection observed on this host since the monitor started"
        elif not self.own_external:
            verdict = (f"{len(self.external)} external connection(s) by other programs on this host; "
                       f"none by the workbench or the model server")
        else:
            verdict = f"{len(self.own_external)} external connection(s) by workbench processes"
        return {
            "running": self.running, "started": self.started, "uptime_seconds": (time.time() - self.started) if self.started else 0,
            "scope": self.scope, "interval_seconds": self.interval, "samples": self.samples, "errors": self.errors,
            "connections_seen": self.connections_seen, "external_connections": len(self.external),
            "workbench_external_connections": len(self.own_external),
            "recent_external": ext, "recent_workbench_external": own_ext, "interface_events": if_events,
            "uplinks_up": sum(1 for i in uplinks if i["isup"]), "uplinks_total": len(uplinks),
            "physically_disconnected": bool(uplinks) and not any(i["isup"] for i in uplinks),
            "interfaces": self.interfaces(),
            "log_path": str(self.log.path), "log_entries": len(self.log), "chain": chain.model_dump(),
            "verdict": verdict,
        }

    def recent(self, limit: int = 100, event: str | None = None) -> list[dict]:
        rows = self.log.read()
        if event:
            rows = [r for r in rows if r.get("event") == event]
        return rows[-limit:]
