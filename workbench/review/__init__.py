"""Human review: provenance-linked drafts, confidence flags, mandatory resolution, visible pending state.

A draft is created ``pending_signoff`` and stays there until a manager or administrator signs
it off — and sign-off is refused while any flagged figure is unresolved. Nothing is ever
auto-approved; a low-confidence OCR value is a flag the reviewer must decide, not a fact.
"""
from workbench.review.drafts import Draft, DraftFigure, DraftRegistry, FigureSource, SignoffBlocked, figures_from_intake

__all__ = ["Draft", "DraftFigure", "DraftRegistry", "FigureSource", "SignoffBlocked", "figures_from_intake"]
