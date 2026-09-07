"""Pydantic schema for document glossary.

The glossary captures terminology discovered from the document itself.
Abbreviations and terms are authoritative for THAT document — the system
does NOT invent expansions from general knowledge.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GlossaryEntry(BaseModel):
    """A single glossary entry with full provenance."""
    term: str = Field(description="The term or abbreviation")
    canonical_meaning: str = Field(description="The authoritative meaning from this document")
    aliases: list[str] = Field(default_factory=list, description="Alternative forms of this term")
    abbreviation: str = Field(default="", description="Abbreviated form if applicable")
    full_form: str = Field(default="", description="Expanded form if applicable")
    evidence: str = Field(description="Source text that establishes this meaning")
    page: int | None = Field(default=None, description="Page where evidence was found")
    section: str = Field(default="", description="Section where evidence was found")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = Field(
        default="abbreviation_table",
        description="How discovered: 'abbreviation_table', 'inline_definition', 'title_page', 'llm_assisted'",
    )
    document_id: str = Field(default="")


class DocumentGlossary(BaseModel):
    """Complete glossary for a document.

    This is built during the document orientation phase and used
    as context for all subsequent extraction. Terms here are
    authoritative for the source document.
    """
    document_id: str
    source_filename: str
    entries: list[GlossaryEntry] = Field(default_factory=list)
    total_entries: int = Field(default=0)

    # Quick lookup maps (populated after building)
    abbreviation_map: dict[str, str] = Field(
        default_factory=dict,
        description="abbreviation -> full_form lookup",
    )
    term_map: dict[str, str] = Field(
        default_factory=dict,
        description="term -> canonical_meaning lookup",
    )

    build_timestamp: str = Field(default="")

    def lookup(self, term: str) -> GlossaryEntry | None:
        """Find a glossary entry by term, abbreviation, or alias."""
        term_upper = term.upper().strip()
        for entry in self.entries:
            if entry.term.upper().strip() == term_upper:
                return entry
            if entry.abbreviation.upper().strip() == term_upper:
                return entry
            if term_upper in [a.upper().strip() for a in entry.aliases]:
                return entry
        return None

    def build_lookup_maps(self) -> None:
        """Populate the quick-lookup dictionaries from entries."""
        self.abbreviation_map = {}
        self.term_map = {}
        for entry in self.entries:
            if entry.abbreviation:
                self.abbreviation_map[entry.abbreviation.upper()] = entry.full_form
            self.term_map[entry.term.upper()] = entry.canonical_meaning
        self.total_entries = len(self.entries)
