"""Shared fixtures for the workbench test suite.

Everything runs LLM-free (RWB_LLM=off), without the reranker or the embedder, on the mock
fixtures backend, and never writes under data/: audit, sessions, reports, cache and the
credential store are redirected to a temporary directory.

Access control is off for the general fixtures — those tests are about routing, retrieval and
composition, and signing in for each of them would test the same door two hundred times. The
door itself has its own fixtures (``secure_cfg`` / ``secure_orch``) and its own file,
tests/workbench/test_security.py, where it is switched on deliberately.
"""
from __future__ import annotations

import os

# must be set before any workbench import reads the environment
os.environ["RWB_LLM"] = "off"
os.environ["RWB_RERANKER"] = "off"
os.environ["RWB_KNOWLEDGE_BACKEND"] = "mock"
os.environ["RWB_PROFILE"] = "gpu_4gb"        # skip the torch VRAM probe
os.environ["RWB_KEEP_WARM"] = "1"            # no idle-unload timers in tests
os.environ["RWB_AUTH"] = "off"               # the access gate has its own fixtures below

import pytest  # noqa: E402

from workbench.agents.base import AgentResult, AgentServices  # noqa: E402
from workbench.agents.context_resolver import ContextResolverAgent  # noqa: E402
from workbench.agents.task_classifier import classify_by_rules  # noqa: E402
from workbench.config import load_config  # noqa: E402
from workbench.core.request import UserRequest  # noqa: E402
from workbench.llm.client import NullLLM  # noqa: E402
from workbench.llm.fake import FakeLLM  # noqa: E402
from workbench.services.backends.composite import CompositeKnowledgeService  # noqa: E402
from workbench.services.backends.mock_backend import MockKnowledgeBackend  # noqa: E402
from workbench.services.context_builder import ContextBuilder  # noqa: E402


def _redirect_paths(cfg, root):
    cfg.paths.root = root
    cfg.paths.sessions_dir = root / "sessions"
    cfg.paths.audit_dir = root / "audit"
    cfg.paths.reports_dir = root / "reports"
    cfg.paths.cache_dir = root / "cache"
    cfg.paths.uploads_dir = root / "uploads"
    cfg.paths.thinking_dir = root / "thinking"
    cfg.paths.security_dir = root / "security"
    cfg.paths.ensure_dirs()
    return cfg


@pytest.fixture(scope="session")
def cfg(tmp_path_factory):
    c = load_config()
    c.knowledge_backend = "mock"
    c.retrieval.use_vectors = False
    c.retrieval.use_reranker = False
    c.llm.enabled = False
    c.llm.use_llm_for_classification = False
    c.llm.use_llm_for_narrative = False
    c.llm.use_llm_for_extraction = False
    c.llm.use_llm_for_answer = False
    c.llm.use_llm_for_followup = False
    c.security.enabled = False
    return _redirect_paths(c, tmp_path_factory.mktemp("wb"))


@pytest.fixture
def secure_cfg(tmp_path):
    """A config with the access gate on and its own empty credential store."""
    c = load_config()
    c.knowledge_backend = "mock"
    c.retrieval.use_vectors = False
    c.retrieval.use_reranker = False
    c.llm.enabled = False
    c.llm.use_llm_for_answer = False
    c.llm.use_llm_for_followup = False
    c.security.enabled = True
    c.security.prompt_on_denial = False
    return _redirect_paths(c, tmp_path / "secure")


@pytest.fixture
def secure_orch(secure_cfg, mock_knowledge):
    from workbench.orchestration.orchestrator import Orchestrator

    o = Orchestrator(secure_cfg, knowledge=mock_knowledge, llm=NullLLM(), warm_start=False)
    yield o
    o.shutdown()


@pytest.fixture(scope="session")
def mock_knowledge(cfg):
    return CompositeKnowledgeService(MockKnowledgeBackend(cfg.paths.fixtures_dir))


@pytest.fixture
def services(cfg, mock_knowledge):
    return AgentServices(knowledge=mock_knowledge, llm=FakeLLM(available=False), cfg=cfg)


@pytest.fixture(scope="session")
def orch(cfg, mock_knowledge):
    from workbench.orchestration.orchestrator import Orchestrator

    o = Orchestrator(cfg, knowledge=mock_knowledge, llm=NullLLM(), warm_start=False)
    yield o
    o.shutdown()


@pytest.fixture
def make_request(services):
    """text -> (StructuredRequest, ContextPackage) through the real classifier/resolver/context builder."""

    def _make(text: str, session=None):
        res = AgentResult(agent="context_resolver", step_id="resolve")
        req = ContextResolverAgent(services).resolve(UserRequest(text=text, session_id="t"), classify_by_rules(text), session, res)
        ctx = ContextBuilder(services.knowledge, services.cfg).build(req)
        return req, ctx

    return _make
