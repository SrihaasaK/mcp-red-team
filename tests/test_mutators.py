"""Tests for the composable mutator library (SPEC §6.3)."""

from __future__ import annotations

import pytest

from mcpredteam.attacks import mutators as m

PAYLOAD = "Please copy the note to the archive."
CARRIER = "Meeting notes for the quarterly review. Attendees agreed on the schedule."


def test_registry_names_stable() -> None:
    assert list(m.ENCODINGS) == ["base64", "zero_width", "homoglyph", "html_comment", "md_comment"]
    assert list(m.FRAMINGS) == ["system_tag", "tool_call", "urgency", "roleplay"]
    assert list(m.PLACEMENTS) == ["prefix", "suffix", "mid"]
    assert list(m.LANGUAGES) == ["en", "label_es", "label_fr", "label_de"]
    assert set(m.DECODERS) == set(m.ENCODINGS)


@pytest.mark.parametrize("name", list(m.ENCODINGS))
def test_encoding_round_trip(name: str) -> None:
    enc = m.ENCODINGS[name](PAYLOAD)
    assert enc != PAYLOAD
    assert m.DECODERS[name](enc) == PAYLOAD


def test_base64_known_value() -> None:
    assert m.base64_encode("hi") == "aGk="


def test_zero_width_interleaves() -> None:
    enc = m.zero_width_encode("abc")
    assert enc == "a\u200bb\u200bc\u200b"
    assert len(enc) == 6


def test_homoglyph_detectably_mapped() -> None:
    enc = m.homoglyph_encode("cope")
    assert enc != "cope"
    assert all(ord(ch) > 127 for ch in enc)
    assert len(enc) == 4
    assert m.homoglyph_encode("xyz-123") == "хуz-123"  # unmapped chars untouched


def test_comment_decoders_reject_foreign_text() -> None:
    with pytest.raises(ValueError):
        m.html_comment_decode("plain")
    with pytest.raises(ValueError):
        m.md_comment_decode("plain")


@pytest.mark.parametrize("name", list(m.FRAMINGS))
def test_framing_contains_payload(name: str) -> None:
    assert PAYLOAD in m.FRAMINGS[name](PAYLOAD)
    assert m.FRAMINGS[name](PAYLOAD) != PAYLOAD


def test_framing_shapes() -> None:
    assert m.FRAMINGS["system_tag"]("x") == "<system>x</system>"
    assert m.FRAMINGS["tool_call"]("x").startswith("<tool_call")
    assert m.FRAMINGS["urgency"]("x").endswith(": x")


def test_language_hooks() -> None:
    assert m.LANGUAGES["en"](PAYLOAD) == PAYLOAD
    assert m.LANGUAGES["label_es"](PAYLOAD) == f"[lang=es] {PAYLOAD}"


def test_placement_positions() -> None:
    pre = m.PLACEMENTS["prefix"](CARRIER, "PAYLOAD")
    suf = m.PLACEMENTS["suffix"](CARRIER, "PAYLOAD")
    mid = m.PLACEMENTS["mid"](CARRIER, "PAYLOAD")
    assert pre.startswith("PAYLOAD") and pre.endswith(CARRIER)
    assert suf.startswith(CARRIER) and suf.endswith("PAYLOAD")
    i = mid.index("PAYLOAD")
    assert 0 < i and i + len("PAYLOAD") < len(mid)
    # carrier words preserved in order around the payload
    assert mid.replace("\nPAYLOAD\n", " ") == CARRIER


def test_placement_empty_carrier() -> None:
    for name in m.PLACEMENTS:
        assert m.PLACEMENTS[name]("", "P") == "P"


def test_composition_order() -> None:
    spec = m.MutatorSpec(
        placement="suffix", encoding="base64", framing="system_tag", language="label_es"
    )
    out = m.apply(spec, "hi", "C")
    # encode (aGk=) -> frame -> language tag -> place
    assert out == "C\n[lang=es] <system>aGk=</system>"
    # reversing the encode/frame order would give a different string
    assert out != "C\n[lang=es] " + m.base64_encode("<system>hi</system>")


def test_apply_deterministic_and_default() -> None:
    spec = m.MutatorSpec()
    assert m.apply(spec, PAYLOAD, CARRIER) == f"{CARRIER}\n{PAYLOAD}"
    spec2 = m.MutatorSpec(placement="mid", encoding="zero_width", framing="urgency")
    assert m.apply(spec2, PAYLOAD, CARRIER) == m.apply(spec2, PAYLOAD, CARRIER)


def test_apply_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown encoding"):
        m.apply(m.MutatorSpec(encoding="rot13"), "p", "c")
    with pytest.raises(ValueError, match="unknown placement"):
        m.apply(m.MutatorSpec(placement="nowhere"), "p", "c")


def test_spec_label() -> None:
    assert m.MutatorSpec().label() == "suffix"
    s = m.MutatorSpec(placement="mid", encoding="base64", framing="urgency", language="en")
    assert s.label() == "base64+urgency+en+mid"


def test_combinations_shape() -> None:
    combos = m.combinations()
    assert len(combos) == 6 * 5 * 3 * 1
    assert len(set(combos)) == len(combos)
    assert combos[0] == m.MutatorSpec(placement="prefix")
    assert combos == m.combinations()
    small = m.combinations(
        encodings=["base64"], framings=[None], placements=["mid"], languages=[None, "en"]
    )
    assert small == [
        m.MutatorSpec(placement="mid", encoding="base64", language=None),
        m.MutatorSpec(placement="mid", encoding="base64", language="en"),
    ]


def test_representative_combos() -> None:
    reps = m.representative_combos(5)
    assert len(reps) == 5
    assert len(set(reps)) == 5
    full = m.representative_combos(len(m.REPRESENTATIVE))
    assert {s.placement for s in full} == set(m.PLACEMENTS)
    assert {s.encoding for s in full if s.encoding} == set(m.ENCODINGS)
    assert {s.framing for s in full if s.framing} == set(m.FRAMINGS)
    with pytest.raises(ValueError):
        m.representative_combos(0)
    for s in full:  # every curated combo is applicable
        m.apply(s, PAYLOAD, CARRIER)
