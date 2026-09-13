"""Tests for specification-block and prose claims with context dimensions."""

from schemas.claims import ClaimCategory
from schemas.normalized_document import ContentType, NormalizedElement
from src.spec_claims import detect_spec_pairs, extract_prose_claims, extract_spec_claims, subject_from_headings

PATH = "Chapter 16: Major equipment > 16.1 CRUDE CHARGE PUMP 11-PM- 01A/B: > Operating Conditions:"


def _el(i, content, ctype=ContentType.PARAGRAPH, page=225):
    return NormalizedElement(element_id=f"e{i}", content_type=ctype, content=content, page=page, position_in_page=i,
                             section_path=PATH, parent_heading="Operating Conditions:")


def test_subject_prefers_tag_in_heading_over_generic_subheading():
    assert subject_from_headings(PATH, "Operating Conditions:") == "11-PM-01A/B"
    assert subject_from_headings("Chapter 16: Major equipment > 16.11 Ejectors: > Description:", "Description:") == "Ejectors"


def test_vertical_label_value_block_pairs_in_order():
    els = [_el(i, t) for i, t in enumerate([
        "Normal flow rate", "Minimum flow rate", "Suction Pressure", "Discharge Pressure", "Differential Head",
        "482 m 3 /hr", "219 m 3 /hr", "2.0 kg/cm 2 A", "24.45 kg/cm 2 A", "367.3 meters",
        "NPSH available", "6 meters",
    ])]
    pairs = detect_spec_pairs(els)
    assert [(p.label, p.value) for p in pairs] == [
        ("Normal flow rate", "482 m 3 /hr"), ("Minimum flow rate", "219 m 3 /hr"),
        ("Suction Pressure", "2.0 kg/cm 2 A"), ("Discharge Pressure", "24.45 kg/cm 2 A"),
        ("Differential Head", "367.3 meters"), ("NPSH available", "6 meters"),
    ]
    claims = extract_spec_claims(els, "c1", "d", PATH, "Operating Conditions:")
    by_label = {c.predicate_raw: c for c in claims}
    assert all(c.subject == "11-PM-01A/B" for c in claims)
    normal = by_label["spec:Normal flow rate"]
    minimum = by_label["spec:Minimum flow rate"]
    assert normal.predicate == ClaimCategory.VOLUMETRIC_FLOW_RATE and normal.unit == "m3/h"
    assert normal.parameter_role == "normal" and minimum.parameter_role == "minimum"
    assert normal.context_key() != minimum.context_key()          # never compared -> never a conflict
    suction = by_label["spec:Suction Pressure"]
    discharge = by_label["spec:Discharge Pressure"]
    assert suction.location == "suction" and discharge.location == "discharge"
    assert suction.pressure_basis == "absolute" and suction.unit == "kg/cm2"
    assert suction.context_key() != discharge.context_key()
    head = by_label["spec:Differential Head"]
    assert head.predicate == ClaimCategory.GENERIC_PROPERTY and head.unit == "m" and head.qualifier == "Differential head"
    assert head.value_numeric == 367.3


def test_inline_label_colon_value():
    els = [_el(0, "Design pressure : 31.7 kg/cm2 A"), _el(1, "Design temperature: 65 °C")]
    claims = extract_spec_claims(els, "c1", "d", PATH, "Operating Conditions:")
    preds = {c.predicate for c in claims}
    assert ClaimCategory.DESIGN_PRESSURE in preds and ClaimCategory.DESIGN_TEMPERATURE in preds
    dp = next(c for c in claims if c.predicate == ClaimCategory.DESIGN_PRESSURE)
    assert dp.parameter_role == "design" and dp.pressure_basis == "absolute"


def test_prose_claims_keep_design_rated_and_enhanced_apart():
    text = ("This crude is fed to a crude charge pump 11-P-01A/B to raise the feed crude pressure to 24 kg/cm2 g. "
            "The rated capacity of the pumps is 482 m 3 /h. However, the pump can be operated at a design limit of 520 m 3 /h. "
            "CDU / VDU-II have a design capacity to process 3.0 MMTPA of crude oil. "
            "The crude processing capacity was enhanced to 3.2 MMTPA by installing Pre-Flash Drum in 1996.")
    claims = extract_prose_claims(text, "c2", "d", "Chapter 6 > 6.1.1 FEED SUPPLY", 61, "6.1.1 FEED SUPPLY")
    by_val = {c.value: c for c in claims}
    assert by_val["24"].subject == "11-P-01A/B" and by_val["24"].pressure_basis == "gauge"
    assert by_val["482"].subject == "11-P-01A/B" and by_val["482"].parameter_role == "rated"
    assert by_val["520"].subject == "11-P-01A/B" and by_val["520"].parameter_role == "design"
    assert by_val["520"].predicate == ClaimCategory.VOLUMETRIC_FLOW_RATE
    assert by_val["3.0"].parameter_role == "design" and by_val["3.0"].unit == "MMTPA"
    assert by_val["3.2"].parameter_role == "rated" and by_val["3.2"].qualifier == "enhanced"
    assert by_val["3.0"].context_key() != by_val["3.2"].context_key()
    assert by_val["482"].context_key() != by_val["520"].context_key()


def test_prose_claim_uses_operating_mode_from_section():
    text = "Maintain column pressure at 1.0 kg/cm2 g by operating PIC-1409."
    claims = extract_prose_claims(text, "c3", "d", "Chapter 12: Initial Start up Procedure > 12.2.4 Cold oil", 176, "")
    assert claims and claims[0].operating_mode == "startup"
    assert claims[0].subject == "column"          # the instrument is the controller, not the subject
    assert claims[0].pressure_basis == "gauge"
