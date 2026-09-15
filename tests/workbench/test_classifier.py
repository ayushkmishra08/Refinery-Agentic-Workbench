"""Rule-based task classifier on the canonical benchmark prompts."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from workbench.agents.task_classifier import classify_by_rules, rule_scores
from workbench.core.request import TaskType

PROMPTS = Path(__file__).resolve().parents[2] / "workbench" / "benchmarks" / "prompts.yaml"


def _unambiguous_prompts() -> list[tuple[str, str]]:
    data = yaml.safe_load(PROMPTS.read_text(encoding="utf-8"))
    out = []
    for cat in data:
        for p in cat["prompts"]:
            if p.get("expected_task") == "ambiguous" or p.get("allow_ambiguous"):
                continue
            out.append((p["text"], p["expected_task"]))
    return out


def test_rule_classifier_accuracy_on_benchmark_is_at_least_85_percent():
    items = _unambiguous_prompts()
    assert len(items) >= 40
    hits = [(t, e, classify_by_rules(t).task_type.value) for t, e in items]
    misses = [(t, e, got) for t, e, got in hits if got != e]
    acc = 1 - len(misses) / len(items)
    assert acc >= 0.85, f"accuracy {acc:.3f}; misses: {misses}"


@pytest.mark.parametrize("text,expected", [
    ("What is the normal flow rate of the crude charge pump?", TaskType.LOOKUP),
    ("Trace the crude flow from the crude charge pump to the atmospheric column.", TaskType.MULTI_HOP),
    ("How do I change over from the running crude charge pump to the standby pump?", TaskType.PROCEDURE),
    ("The crude charge pump discharge pressure is dropping. What should I check?", TaskType.TROUBLESHOOTING),
    ("The crude charge pump is operating at 520 m3/h. Is this acceptable?", TaskType.LIMITS),
    ("Why is the crude heated before entering the atmospheric column?", TaskType.EXPLANATION),
    ("What safety precautions are required before working on the crude charge pump?", TaskType.SAFETY),
    ("Compare the documented operating conditions for Basrah crude and Bombay High crude.", TaskType.COMPARISON),
    ("I found two different normal flow values for the crude charge pump. Which one should I trust?", TaskType.CONFLICT),
    ("Show me all documented values for the pump discharge pressure and their sources.", TaskType.PROVENANCE),
    ("Prepare an engineering investigation plan for repeated trips of the vacuum heater.", TaskType.PLANNING),
    ("Prepare a report on the atmospheric column operating envelope.", TaskType.REPORT),
    ("Which documents describe the startup procedure for the atmospheric heater?", TaskType.CROSS_DOCUMENT),
])
def test_representative_prompt_per_task_type(text, expected):
    out = classify_by_rules(text)
    assert out.task_type == expected
    assert out.method == "rules" and 0 < out.confidence <= 0.98


def test_no_pattern_defaults_to_low_confidence_lookup():
    out = classify_by_rules("Tell me something.")
    assert out.task_type == TaskType.LOOKUP and out.confidence < 0.4


def test_rule_scores_returns_hits_per_type():
    scores = rule_scores("Compare the operating limits of the two crude charge pumps.")
    assert TaskType.COMPARISON in scores and TaskType.LIMITS in scores
    assert scores[TaskType.COMPARISON][0] > scores[TaskType.LIMITS][0]


def test_secondary_types_for_compound_request():
    out = classify_by_rules("We need to take the running crude charge pump out of service for maintenance. Determine the correct changeover procedure, isolation requirements and relevant operating limits.")
    assert {out.task_type, *out.secondary} & {TaskType.PROCEDURE, TaskType.SAFETY}
    assert len(out.secondary) <= 3
