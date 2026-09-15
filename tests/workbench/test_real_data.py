"""Optional: the files backend on the real CDU artefacts (skipped when they are absent).

Keyword retrieval only (no embedder / reranker), LLM off. Reuses the workbench's on-disk index
cache when present by copying it into the temporary cache dir, so nothing under data/ is written.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOC = "CDU operating manual"

# knowledge_layer/pipeline.py (imported by the files backend) replaces sys.stdout/sys.stderr with new
# TextIOWrappers around the same buffers at import time; when those wrappers are garbage-collected they
# close pytest's capture files. Import it here, put pytest's streams back and detach the replacements.
_saved_streams = (sys.stdout, sys.stderr)
try:
    import knowledge_layer.pipeline  # noqa: F401  (import-time side effect, see above)
except Exception:  # pragma: no cover - artefacts/modules missing: the module is skipped below anyway
    pass
_hijacked = (sys.stdout, sys.stderr)
sys.stdout, sys.stderr = _saved_streams
for _w in _hijacked:
    if _w not in _saved_streams:
        try:
            _w.detach()
        except Exception:
            pass
pytestmark = pytest.mark.skipif(not (ROOT / "data" / "knowledge" / DOC / "chunks.json").exists() or not (ROOT / "data" / "normalized" / f"{DOC}_normalized.json").exists(),
                                reason="knowledge-layer artefacts for the CDU manual are not present")


@pytest.fixture(scope="module")
def real_orch(tmp_path_factory):
    os.environ["RWB_LLM"] = "off"
    os.environ["RWB_RERANKER"] = "off"
    from workbench.config import load_config
    from workbench.llm.client import NullLLM
    from workbench.orchestration.orchestrator import Orchestrator

    cfg = load_config()
    root = tmp_path_factory.mktemp("real")
    cfg.paths.sessions_dir, cfg.paths.audit_dir, cfg.paths.reports_dir, cfg.paths.cache_dir, cfg.paths.uploads_dir = (root / "s", root / "a", root / "r", root / "c", root / "u")
    cfg.paths.ensure_dirs()
    existing = ROOT / "data" / "workbench" / "cache" / DOC / "index.json"
    if existing.exists():
        (cfg.paths.cache_dir / DOC).mkdir(parents=True, exist_ok=True)
        shutil.copy(existing, cfg.paths.cache_dir / DOC / "index.json")
    cfg.knowledge_backend = "files"
    cfg.document_ids = [DOC]
    cfg.retrieval.use_vectors = False
    cfg.retrieval.use_reranker = False
    cfg.llm.enabled = False
    o = Orchestrator(cfg, llm=NullLLM(), warm_start=False)
    yield o
    o.shutdown()


def test_real_lookup_normal_flow_of_crude_charge_pump(real_orch):
    from workbench.core.request import UserRequest

    resp = real_orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="real-1"))
    assert resp.status == "answered" and resp.task_type.value == "lookup"
    assert "482" in resp.answer_markdown and resp.evidence
    assert any("11-P" in e or "Crude" in e for e in resp.entities)


def test_real_scenario_comparison_basrah_vs_bombay_high(real_orch):
    from workbench.core.request import UserRequest

    resp = real_orch.ask(UserRequest(text="Compare the documented operating conditions for Basrah crude and Bombay High crude.", session_id="real-2"))
    assert resp.status == "answered" and resp.task_type.value == "comparison"
    comp = next(b for b in resp.blocks if b.type == "comparison")
    assert set(comp.subjects) >= {"Basrah", "Bombay High"} and len(comp.attributes) >= 10


def test_real_ambiguous_request_is_clarified(real_orch):
    from workbench.core.request import UserRequest

    resp = real_orch.ask(UserRequest(text="Can I run this at 500?", session_id="real-3"))
    assert resp.status == "clarification" and resp.blocks[0].type == "clarification"
