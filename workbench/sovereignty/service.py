"""SovereigntyService: the one object the API and CLI ask "is this deployment provably local?".

Brings together the egress guard, the network monitor, and a verification pass over every
hash-chained log the workbench keeps (security audit, run audits, connection log, sandbox runs,
routing, key events, model updates, draft events). ``report()`` is what the Sovereignty page
renders and what ``workbench sovereignty`` prints.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from workbench.sovereignty.egress import EgressGuard, install_egress_guard
from workbench.sovereignty.hashchain import ChainVerification, HashChainedLog
from workbench.sovereignty.netmonitor import NetworkMonitor

logger = logging.getLogger(__name__)


class SovereigntyService:
    def __init__(self, cfg, *, start_monitor: bool = True) -> None:
        self.cfg = cfg
        s = cfg.sovereignty
        self.dir = Path(s.sovereignty_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.guard: EgressGuard | None = None
        self.guard_log = HashChainedLog(self.dir / "egress.jsonl", name="egress")
        if s.airgap_enforced:
            allowed = list(s.allowed_hosts)
            for url in (getattr(cfg.llm, "base_url", ""), getattr(getattr(cfg, "knowledge_layer", None), "neo4j", None) and getattr(cfg.knowledge_layer.neo4j, "uri", "")):
                host = _host_of(url)
                if host:
                    allowed.append(host)
            self.guard = install_egress_guard(allowed, self.guard_log)
        self.monitor: NetworkMonitor | None = None
        if s.monitor_enabled:
            self.monitor = NetworkMonitor(self.dir, interval=s.monitor_interval_seconds, scope=s.monitor_scope)
            if start_monitor:
                self.monitor.start()
        self.started = time.time()

    # ------------------------------------------------------------------ chained logs
    def chained_logs(self) -> list[tuple[str, Path]]:
        p = self.cfg.paths
        out: list[tuple[str, Path]] = [
            ("security audit", p.security_dir / "security.jsonl"),
            ("connection log", self.dir / "connections.jsonl"),
            ("egress guard", self.dir / "egress.jsonl"),
            ("routing log", Path(self.cfg.models.routing_log)),
            ("sandbox runs", Path(self.cfg.sandbox.root_dir) / "runs.jsonl"),
            ("key events", p.security_dir / "kms" / "key_events.jsonl"),
            ("model updates", p.security_dir / "model_updates.jsonl"),
            ("draft events", Path(self.cfg.review.drafts_dir) / "events.jsonl"),
            ("tool calls", Path(self.cfg.sandbox.workspace_dir) / "tool_calls.jsonl"),
        ]
        return out

    def run_audit_logs(self) -> list[Path]:
        return [f for f in sorted(Path(self.cfg.paths.audit_dir).glob("*.jsonl")) if f.name != "hitl.jsonl"]

    def verify_all(self) -> list[dict]:
        rows = []
        for name, path in self.chained_logs():
            if not path.exists():
                rows.append({"log": name, "path": str(path), "exists": False, "ok": True, "entries": 0, "detail": "not created yet"})
                continue
            v: ChainVerification = HashChainedLog(path).verify()
            rows.append({"log": name, "path": str(path), "exists": True, **v.model_dump(exclude={"path"})})
        # the per-session run audits are many; one aggregated row keeps the report readable
        audits = self.run_audit_logs()
        if audits:
            bad: list[str] = []
            entries = 0
            for f in audits:
                v = HashChainedLog(f).verify()
                entries += v.entries
                if not v.ok:
                    bad.append(f"{f.stem} (seq {v.first_bad_seq}: {v.detail})")
            rows.append({"log": f"run audits ({len(audits)} conversations)", "path": str(self.cfg.paths.audit_dir), "exists": True,
                         "ok": not bad, "entries": entries, "head": "", "first_bad_seq": None,
                         "detail": "all intact" if not bad else "tampered: " + "; ".join(bad[:5])})
        return rows

    # ------------------------------------------------------------------ report
    def report(self) -> dict:
        chains = self.verify_all()
        mon = self.monitor.status() if self.monitor else {"running": False, "verdict": "monitor disabled"}
        guard = self.guard.status() if self.guard else {"installed": False}
        return {
            "airgap_enforced": bool(self.cfg.sovereignty.airgap_enforced),
            "egress_guard": guard,
            "network_monitor": mon,
            "chains": chains,
            "chains_intact": all(r["ok"] for r in chains),
            "verdict": _verdict(guard, mon, chains),
            "generated": time.time(),
        }

    def shutdown(self) -> None:
        if self.monitor:
            self.monitor.stop()


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url if "://" in url else f"//{url}")
        return parsed.hostname
    except Exception:
        return None


def _verdict(guard: dict, mon: dict, chains: list[dict]) -> str:
    parts = []
    parts.append("egress guard installed" if guard.get("installed") else "egress guard OFF")
    if mon.get("running"):
        parts.append(mon.get("verdict", ""))
        if mon.get("physically_disconnected"):
            parts.append("every uplink interface is down (cable pulled)")
    else:
        parts.append("network monitor not running")
    bad = [c["log"] for c in chains if not c["ok"]]
    parts.append("all audit chains intact" if not bad else f"TAMPERED: {', '.join(bad)}")
    return "; ".join(p for p in parts if p)
