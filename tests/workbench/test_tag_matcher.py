"""Near-miss tag matching: the engineer typed a tag the manual does not contain.

The behaviour under test is the judgement call, not the arithmetic: adopt a candidate and say
so when one is clearly the item meant, and ask when two are equally plausible. Answering about
the wrong pump is worse than one extra question; asking when there is only one candidate is a
wasted turn.
"""
from __future__ import annotations

from workbench.core.knowledge import EntityRecord
from workbench.services.tag_matcher import (
    adopt,
    looks_like_tag,
    parse_tag,
    score_tag,
    suggest_names,
    suggest_tags,
    type_hint,
)


def _e(tag: str, name: str, etype: str, mentions: int = 5) -> EntityRecord:
    return EntityRecord(entity_uid=f"e-{tag}", name=f"{name} ({tag})", canonical_tag=tag,
                        entity_type=etype, aliases=[tag, tag.replace("-", "")], mention_count=mentions)


POOL = [
    _e("11-P-01", "Crude Charge Pump", "Pump", 13),
    _e("11-PM-01", "Crude Feed Pump", "Pump", 37),
    _e("12-P-01", "Quench Pumps", "Pump", 16),
    _e("12-PM-01", "SR Pumps", "Pump", 10),
    _e("12-E-01", "Crude/Kero CR", "Exchanger", 16),
    _e("11-E-01", "Crude/HN", "Exchanger", 11),
    _e("12-F-01", "Vacuum Heater", "Heater", 106),
    _e("12-C-01", "Vacuum Column", "Column", 98),
    _e("11-C-01", "Atmospheric Column", "Column", 83),
    _e("11-C-05", "Stabilizer Column", "Column", 46),
]


# --------------------------------------------------------------------------- parsing
def test_a_tag_is_split_into_unit_class_number_and_train():
    p = parse_tag("11-P-01A")
    assert (p.unit, p.cls, p.number, p.train) == ("11", "P", "01", "A")
    assert parse_tag("11 P 01").normalized() == parse_tag("11-P-01").normalized()
    assert parse_tag("11P01").normalized() == parse_tag("11-P-01").normalized()
    assert parse_tag("12-3-01").cls == "3", "a digit in the class position is kept, not discarded"
    assert parse_tag("PIC-1105").cls == "PIC"
    assert parse_tag("the crude charge pump") is None


def test_looks_like_tag_separates_tags_from_phrases():
    assert looks_like_tag("12-3-01") and looks_like_tag("11-P-01A")
    assert not looks_like_tag("crude charge pump")
    assert not looks_like_tag("")


def test_the_equipment_word_in_the_sentence_is_picked_up():
    assert type_hint("what does pump 12-3-01 do") == "Pump"
    assert type_hint("what does the exchanger 12-3-01 do") == "Exchanger"
    assert type_hint("what does 12-3-01 do") is None


# --------------------------------------------------------------------------- scoring
def test_agreement_on_unit_and_number_outweighs_the_class_letter():
    mention = parse_tag("12-3-01")
    same_unit_and_number, _ = score_tag(mention, parse_tag("12-P-01"))
    same_letter_only, _ = score_tag(mention, parse_tag("13-3-99"))
    assert same_unit_and_number > same_letter_only


def test_an_exact_tag_scores_one():
    score, _ = score_tag(parse_tag("11-E-1"), parse_tag("11-E-01"))
    assert score == 1.0, "leading zeros are not a difference"


def test_the_class_named_in_the_sentence_breaks_a_tie_without_flattening_the_ranking():
    """A flat type bonus used to push every strong candidate to 1.0 and destroy the ordering."""
    exact_class, _ = score_tag(parse_tag("12-P-1"), parse_tag("12-P-01"), expect_type="Pump", entity_type="Pump")
    family_class, _ = score_tag(parse_tag("12-P-1"), parse_tag("12-PM-01"), expect_type="Pump", entity_type="Pump")
    assert exact_class > family_class
    assert exact_class <= 1.0 and family_class <= 1.0


# --------------------------------------------------------------------------- suggesting
def test_a_mistyped_pump_tag_suggests_the_documented_pumps_first():
    top = suggest_tags("12-3-01", POOL, expect_type="Pump")
    assert [s.entity.canonical_tag for s in top][:2] == ["12-P-01", "12-PM-01"]
    assert all(s.entity.entity_type == "Pump" for s in top[:2])


def test_the_same_tag_with_a_different_equipment_word_suggests_a_different_class():
    assert suggest_tags("12-3-01", POOL, expect_type="Exchanger")[0].entity.canonical_tag == "12-E-01"


def test_a_suggestion_explains_itself_in_one_line():
    line = suggest_tags("12-3-01", POOL, expect_type="Pump")[0].describe()
    assert "12-P-01" in line and "Quench Pumps" in line and "pump" in line
    assert line.count(";") == 0, "one line, not a list of reasons"


def test_the_ab_trains_of_one_item_are_offered_once():
    pool = POOL + [_e("11-P-01A", "Crude Charge Pump A", "Pump"), _e("11-P-01B", "Crude Charge Pump B", "Pump")]
    tags = [s.entity.canonical_tag for s in suggest_tags("11-P-1", pool, expect_type="Pump")]
    assert tags.count("11-P-01") + tags.count("11-P-01A") + tags.count("11-P-01B") == 1


def test_nothing_is_suggested_for_a_tag_that_resembles_nothing():
    assert suggest_tags("crude charge pump", POOL) == []


# --------------------------------------------------------------------------- adopting vs asking
def test_one_clear_candidate_is_adopted():
    chosen = adopt(suggest_tags("11-E-1", POOL, expect_type="Exchanger"))
    assert chosen is not None and chosen.entity.canonical_tag == "11-E-01"


def test_two_equally_plausible_candidates_are_not_adopted():
    """12-P-01 and 12-PM-01 are both unit-12 pumps numbered 01; the engineer has to say which."""
    assert adopt(suggest_tags("12-3-01", POOL, expect_type="Pump")) is None


def test_a_weak_best_match_is_not_adopted():
    assert adopt(suggest_tags("11-C-99", POOL, expect_type="Column")) is None


def test_adopt_handles_an_empty_list():
    assert adopt([]) is None


# --------------------------------------------------------------------------- misspelt names
def test_a_misspelt_name_finds_the_documented_one():
    assert suggest_names("vaccum column", POOL)[0].entity.canonical_tag == "12-C-01"
    assert suggest_names("crude charge pmp", POOL)[0].entity.entity_type == "Pump"


def test_an_unrelated_phrase_matches_no_name():
    assert suggest_names("weather forecast", POOL) == []
