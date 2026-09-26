"""Drafts: provenance-linked figures, confidence flags, mandatory resolution, visible pending state."""
from __future__ import annotations

import pytest

from workbench.core.request import UserRequest
from workbench.intake.ocr import OcrLine, OcrResult
from workbench.intake.pipeline import IntakeResult
from workbench.review.drafts import DraftFigure, DraftRegistry, FigureSource, SignoffBlocked, figures_from_intake


@pytest.fixture(scope="module")
def limits_resp(orch):
    return orch.ask(UserRequest(text="The crude charge pump is operating at 520 m3/h. Is this acceptable?", session_id="review-limits"))


@pytest.fixture
def registry(tmp_path):
    return DraftRegistry(tmp_path / "drafts")


def test_draft_is_pending_with_provenance(registry, limits_resp):
    d = registry.create("docx", "Limit check", response=limits_resp, session_id="s", owner="user", owner_role="user")
    assert d.status == "pending_signoff"
    assert len(d.figures) >= 3
    backed = [f for f in d.figures if f.source.source_kind != "requester"]
    assert all(f.source.document_id for f in backed), "every documented figure links to its document"
    assert d.open_flags == [], "claims from the knowledge layer are not flagged"
    assert registry.get(d.draft_id).public()["pending"] is True
    assert registry.summary()["pending_signoff"] == 1


def test_flag_blocks_signoff_until_resolved(registry, limits_resp):
    d = registry.create("xlsx", "Limit check", response=limits_resp, session_id="s", owner="user", owner_role="user")
    low = DraftFigure(label="Suction pressure (OCR)", value="2.8", unit="kg/cm2",
                      source=FigureSource(document_id="scan.pdf", page=3, text="Suction 2.8 kg/cm2", source_kind="ocr"),
                      confidence=0.4, flagged=True, flag_reason="low OCR confidence 0.40")
    d.figures.append(low)
    registry._save(d)
    d = registry.get(d.draft_id)
    assert [f.figure_id for f in d.open_flags] == [low.figure_id]
    with pytest.raises(SignoffBlocked) as exc:
        registry.sign_off(d.draft_id, by="manager", role="manager")
    assert exc.value.open_flags[0].figure_id == low.figure_id
    assert registry.get(d.draft_id).status == "pending_signoff"
    with pytest.raises(ValueError):
        registry.resolve_figure(d.draft_id, low.figure_id, by="manager", action="corrected")     # needs a value
    registry.resolve_figure(d.draft_id, low.figure_id, by="manager", action="corrected", corrected_value="2.0", note="read from p.3 table")
    with pytest.raises(PermissionError):
        registry.sign_off(d.draft_id, by="user", role="user")
    signed = registry.sign_off(d.draft_id, by="manager", role="manager", note="checked against the table")
    assert signed.status == "signed_off" and signed.signed_off_by == "manager"
    fig = next(f for f in signed.figures if f.figure_id == low.figure_id)
    assert fig.effective_value == "2.0" and fig.resolution.action == "corrected"
    events = registry.events.read()
    assert [e["event"] for e in events][-3:] == ["figure_resolved", "signoff_blocked", "draft_signed_off"] or \
        "signoff_blocked" in [e["event"] for e in events]
    assert registry.verify_chain().ok


def test_figures_with_no_evidence_are_flagged(registry):
    figs = [DraftFigure(label="made up", value="99"), DraftFigure(label="fine", value="1", source=FigureSource(document_id="d", page=1), citation="[1]")]
    d = registry.create("answer", "x", figures=figs, owner="u", owner_role="user")
    assert len(d.open_flags) == 1 and d.open_flags[0].label == "made up"
    d = registry.reject(d.draft_id, by="admin", role="admin", note="unsupported")
    assert d.status == "rejected"
    with pytest.raises(ValueError):
        registry.sign_off(d.draft_id, by="admin", role="admin")


def test_figures_from_intake_flags_low_confidence():
    ocr = OcrResult(source="scan.pdf", lines=[
        OcrLine(text="Design pressure 31.7 kg/cm2", confidence=0.97, page=0),
        OcrLine(text="Suction 2.0 kg/cm2A", confidence=0.4, page=0),
        OcrLine(text="no number here", confidence=0.2, page=0),
    ], threshold=0.6)
    figs = figures_from_intake(IntakeResult(source="scan.pdf", kind="pdf_scanned", ocr=ocr, threshold=0.6))
    assert len(figs) == 2
    flagged = [f for f in figs if f.flagged]
    assert len(flagged) == 1 and flagged[0].value == "2.0" and flagged[0].source.source_kind == "ocr"
    assert flagged[0].source.document_id == "scan.pdf" and flagged[0].source.page == 0
