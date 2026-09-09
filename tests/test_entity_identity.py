"""Tests for canonical tag identity."""

import pytest

from src.entity_identity import (
    document_scoped_uid,
    entity_uid,
    find_tags,
    identify_tag,
    normalize_tag,
)


@pytest.mark.parametrize("raw, canonical", [
    ("11-V-02", "11-V-02"),
    ("11 V 02", "11-V-02"),
    ("11-v-02", "11-V-02"),
    ("11 – V – 02", "11-V-02"),
    ("11_V_02", "11-V-02"),
    (" 11-V-02. ", "11-V-02"),
    ("11-CP-301", "11-CP-301"),
    ("12-P-04 A/B", "12-P-04"),          # train list -> parent
    ("10-P-01A/B", "10-P-01"),
    ("10-P-01 A & B", "10-P-01"),
    ("E-201A", "E-201A"),
    ("e 201 a", "E-201A"),
    ("TIC-1001", "TIC-1001"),
    ("P-101", "P-101"),
    ("FCV 2001", "FCV-2001"),
])
def test_normalize_tag_variants(raw, canonical):
    assert normalize_tag(raw, unit_scoping=False) == canonical


@pytest.mark.parametrize("raw", [
    "Pump",
    "Crude Distillation Unit",
    "Page 8 of 562",
    "31-03-2012",          # date
    "CDU-II",              # roman numeral, no number
    "ISO 9001",            # standard, not a tag
    "H2S",                 # chemical formula
    "Rev 0",
    "",
])
def test_normalize_tag_negatives(raw):
    assert normalize_tag(raw, unit_scoping=False) is None


def test_train_expansion_children_and_parent():
    ident = identify_tag("10-P-01A/B", unit_scoping=False)
    assert ident.canonical == "10-P-01"
    assert ident.children == ["10-P-01A", "10-P-01B"]
    assert ident.parent is None
    assert ident.is_train_list
    child = identify_tag("E-201A", unit_scoping=False)
    assert child.canonical == "E-201A" and child.parent == "E-201" and child.children == []


def test_unit_scoping_only_without_plant_prefix():
    assert normalize_tag("P-101", unit="CDU II") == "CDU-II/P-101"
    assert normalize_tag("11-V-02", unit="CDU II") == "11-V-02"
    assert normalize_tag("P-101", unit=None) == "P-101"
    ident = identify_tag("P-101A/B", unit="10, 11 & 12 CDU II")
    assert ident.canonical == "10-11-12-CDU-II/P-101"
    assert ident.children == ["10-11-12-CDU-II/P-101A", "10-11-12-CDU-II/P-101B"]


def test_uid_is_global_and_stable():
    a = entity_uid(normalize_tag("11-V-02", unit_scoping=False))
    b = entity_uid(normalize_tag("11 v 02", unit_scoping=False))
    assert a == b and len(a) == 16
    assert document_scoped_uid("Crude Desalter", "doc1") != document_scoped_uid("Crude Desalter", "doc2")


def test_find_tags_in_text():
    text = "Feed goes to 11-V-02 (desalter) via pumps 11-P-01 A/B; see TIC-1001 and P&ID 1234. Rev 0, page 12 of 562."
    tags = find_tags(text)
    assert tags == ["11-V-02", "11-P-01", "TIC-1001"]
