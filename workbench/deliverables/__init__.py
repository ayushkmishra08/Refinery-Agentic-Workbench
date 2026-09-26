"""Real deliverables: a FinalResponse becomes a Word, PowerPoint or Excel file.

Every figure in the document keeps its citation, every calculation shows its steps, and every
file carries its classification and a visible "pending human sign-off" status until a reviewer
signs it off in the review registry (``workbench.review``).
"""
from workbench.deliverables.common import DeliverableSpec, calculation_steps_from_response, figures_from_response
from workbench.deliverables.export import ExportResult, export_markdown, export_response

__all__ = ["DeliverableSpec", "ExportResult", "calculation_steps_from_response", "export_markdown",
           "export_response", "figures_from_response"]
