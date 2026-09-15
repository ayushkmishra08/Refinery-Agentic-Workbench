"""Index builder helpers (pure functions)."""
from __future__ import annotations

import pytest

from workbench.services.index.builder import (
    INSTRUMENT_TYPES,
    NAMED_EQUIPMENT_RE,
    PREFIX_TYPES,
    VERB_LIKE,
    canonical_tag,
    clean_alias_phrase,
    norm_alias,
)
from workbench.services.index.store import tokenize


@pytest.mark.parametrize("text,expected", [
    ("Crude Charge Pumps", "crude charge pump"),
    ("11 - PM - 01A/B", "11-pm-01a/b"),
    ("  Atmospheric   Column ", "atmospheric column"),
    ("Desalter (11-V-02)", "desalter 11-v-02"),
])
def test_norm_alias(text, expected):
    assert norm_alias(text) == expected


def test_clean_alias_phrase_strips_leading_stopwords_and_bad_words():
    assert clean_alias_phrase("of crude charge pump") == "crude charge pump"
    assert clean_alias_phrase("To crude line at crude feed pump") == "crude feed pump"
    assert clean_alias_phrase("Atmospheric Column") == "Atmospheric Column"
    assert clean_alias_phrase("cooled in cooler") == "cooler"
    # verbs are not cut by clean_alias_phrase; the builder rejects them through VERB_LIKE
    assert clean_alias_phrase("enters atmospheric column") == "enters atmospheric column" and "enters" in VERB_LIKE


@pytest.mark.parametrize("text,expected", [
    ("11-PM-01A/B", "11-PM-01"),
    ("11 - P - 01", "11-P-01"),
    ("12-C-01", "12-C-01"),
    ("E-201A", "E-201A"),
    ("crude pump", None),
    ("ISO-9001", None),
])
def test_canonical_tag(text, expected):
    assert canonical_tag(text) == expected


def test_prefix_types():
    assert PREFIX_TYPES["P"] == "Pump" and PREFIX_TYPES["PM"] == "Pump"
    assert PREFIX_TYPES["C"] == "Column" and PREFIX_TYPES["F"] == "Heater"
    assert PREFIX_TYPES["PIC"] == "Controller" and PREFIX_TYPES["PSV"] == "ReliefValve"
    assert PREFIX_TYPES["PIC"] in INSTRUMENT_TYPES and PREFIX_TYPES["PV"] in INSTRUMENT_TYPES


def test_named_equipment_regex_finds_multiword_equipment():
    hits = [m.group(1).lower() for m in NAMED_EQUIPMENT_RE.finditer("The crude charge pump feeds the pre-flash drum before the atmospheric heater.")]
    assert "crude charge pump" in hits and "atmospheric heater" in hits
    assert any("flash drum" in h for h in hits)


def test_tokenize_keeps_tags_and_drops_stopwords():
    toks = tokenize("What is the normal flow of 11-PM-01A/B?")
    assert "11-pm-01a/b" in toks and "normal" in toks and "flow" in toks
    assert "the" not in toks and "is" not in toks
