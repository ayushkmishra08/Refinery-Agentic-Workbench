"""Model registry and router: capability-profile routing, pluggability, logging, decomposition."""
from __future__ import annotations

from pydantic import BaseModel

from workbench.models.registry import ModelProfile, ModelRegistry, load_registry
from workbench.models.routed import RoutedLLM
from workbench.models.router import ModelRouter, kind_of_purpose


def _router(tmp_path, installed=("qwen3:4b", "qwen3.5:2b", "deepseek-r1:7b"), vram=3500, **kw):
    return ModelRouter(load_registry(), installed=list(installed), vram_mb=vram, default_model="qwen3:4b",
                       vision_default="qwen3.5:2b", log_path=tmp_path / "routing.jsonl", **kw)


def test_registry_loads_builtin_models():
    reg = load_registry()
    assert reg.get("qwen3:4b") is not None
    assert reg.get("qwen3.5:2b").has_vision
    assert not reg.get("qwen3:4b").has_vision


def test_purpose_mapping():
    assert kind_of_purpose("compose_answer") == "composition"
    assert kind_of_purpose("classify") == "classification"
    assert kind_of_purpose("vision") == "vision"
    assert kind_of_purpose("plan_refine") == "planning"
    assert kind_of_purpose("something_unknown") == "composition"


def test_vision_routes_to_vision_capable_model(tmp_path):
    r = _router(tmp_path)
    d = r.route("vision")
    assert d.chosen == "qwen3.5:2b"
    text = r.route("composition")
    assert text.chosen == "qwen3:4b"
    assert any(c.name == "qwen3:4b" and c.note == "no image input" for c in d.candidates)


def test_uninstalled_models_never_win(tmp_path):
    r = _router(tmp_path, installed=("qwen3:4b",))
    d = r.route("code")
    assert d.chosen == "qwen3:4b"
    coder = next(c for c in d.candidates if c.name == "qwen2.5-coder:7b")
    assert not coder.installed and coder.score == 0


def test_bigger_card_prefers_the_specialist(tmp_path):
    r = _router(tmp_path, installed=("qwen3:4b", "qwen2.5-coder:7b", "deepseek-r1:7b"), vram=12000)
    assert r.route("code").chosen == "qwen2.5-coder:7b"
    assert r.route("reasoning").chosen == "deepseek-r1:7b"
    assert r.route("classification").chosen == "qwen3:4b"


def test_swap_margin_keeps_resident_model(tmp_path):
    r = _router(tmp_path, installed=("qwen3:4b", "deepseek-r1:7b"), vram=12000, swap_margin=0.5)
    r.note_resident("qwen3:4b")
    d = r.route("reasoning")
    assert d.chosen == "qwen3:4b" and "resident" in d.reason


def test_pluggable_registration(tmp_path):
    reg = load_registry()
    reg.register(ModelProfile(name="my-new-model:1b", size_gb=1.0, min_vram_mb=1000,
                              capabilities={"calculation": 0.99, "classification": 0.1}))
    r = ModelRouter(reg, installed=["my-new-model:1b", "qwen3:4b"], vram_mb=3500, default_model="qwen3:4b",
                    vision_default=None)
    assert r.route("calculation").chosen == "my-new-model:1b"
    assert r.route("classification").chosen == "qwen3:4b"


def test_local_override_file_merges(tmp_path):
    local = tmp_path / "registry.local.yaml"
    local.write_text("models:\n  - name: qwen3:4b\n    capabilities: {code: 0.99}\n  - name: extra:1b\n    capabilities: {vision: 0.9}\n    modalities: [text, image]\n", encoding="utf-8")
    reg = load_registry(local)
    assert reg.get("qwen3:4b").capability("code") == 0.99
    assert reg.get("qwen3:4b").source == "local"
    assert reg.get("extra:1b").has_vision


def test_decisions_are_logged_in_a_chain(tmp_path):
    r = _router(tmp_path)
    r.route("composition", purpose="compose_answer", session="s", run_id="run-1")
    r.route("vision", purpose="vision", session="s", run_id="run-1")
    rows = r.log.read()
    assert [row["chosen"] for row in rows] == ["qwen3:4b", "qwen3.5:2b"]
    assert rows[0]["run_id"] == "run-1" and rows[0]["candidates"]
    assert r.log.verify().ok
    assert len(r.decisions_since(0)) == 2


def test_hybrid_request_is_decomposed(tmp_path):
    r = _router(tmp_path)
    plan = r.plan("Read the scanned P&ID, calculate the margin between 482 and 520 m3/h and write a short note")
    kinds = [s.kind for s in plan.subtasks]
    assert plan.hybrid and "vision" in kinds and "calculation" in kinds and "composition" in kinds
    vision = next(d for s, d in zip(plan.subtasks, plan.decisions) if s.kind == "vision")
    assert vision.chosen == "qwen3.5:2b"
    calc = next(s for s in plan.subtasks if s.kind == "calculation")
    assert calc.deterministic_tool == "calculate"
    simple = r.plan("What is the normal flow rate of the crude charge pump?")
    assert not simple.hybrid


class _Out(BaseModel):
    answer: str


class _FakeClient:
    """Stands in for OllamaClient: records which model each call was routed to."""
    model = "qwen3:4b"
    vision_model = "qwen3.5:2b"

    def __init__(self):
        from workbench.llm.client import LLMStats
        self.stats = LLMStats()
        self.calls = []
        self._client = None

    def available(self):
        return True

    def has_model(self, m):
        return m in ("qwen3:4b", "qwen3.5:2b", "deepseek-r1:7b")

    def installed_models(self):
        return ["qwen3:4b", "qwen3.5:2b", "deepseek-r1:7b"]

    def structured(self, system, user, schema, *, max_tokens=None, temperature=None, purpose="", model=None):
        self.calls.append((purpose, model))
        return schema(answer="ok")

    def complete(self, system, user, *, max_tokens=None, temperature=None, purpose="", model=None):
        self.calls.append((purpose, model))
        return "text"

    def describe_image(self, path, prompt, *, max_tokens=600, model=None):
        self.calls.append(("vision", model))
        return "an image"

    def unload(self):
        pass


def test_routed_llm_chooses_per_call(tmp_path):
    client = _FakeClient()
    llm = RoutedLLM(client, _router(tmp_path))
    llm.structured("s", "u", _Out, purpose="classify")
    llm.complete("s", "u", purpose="why_summary")
    llm.describe_image("x.png", "describe")
    assert client.calls == [("classify", "qwen3:4b"), ("why_summary", "qwen3:4b"), ("vision", "qwen3.5:2b")]
    assert llm.model == "qwen3.5:2b"           # attribution follows the last call
    assert llm.router.resident == "qwen3.5:2b"
