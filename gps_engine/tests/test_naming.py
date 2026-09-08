"""Offline tests for the name-resolution layer (English/Devanagari -> portal option)."""

from gps_engine.naming import resolve


def _t(options, query, expect_contains, min_score=0.72):
    m = resolve(options, query)
    assert m is not None, f"{query!r} did not resolve against {options}"
    assert expect_contains in m.text, f"{query!r} -> {m.text!r}, expected to contain {expect_contains!r}"
    assert m.score >= min_score


def test_english_to_devanagari_high_confidence():
    _t(["--Select--", "05 अकोला", "07 अमरावती", "26 अहमदनगर"], "Akola", "अकोला", 0.9)
    _t(["188 गोरखपुर", "186 देवरिया"], "Gorakhpur", "गोरखपुर", 0.9)
    _t(["57 कबीरधाम", "01 बालोद"], "Kabirdham", "कबीरधाम", 0.9)
    _t(["01 अजमेर", "02 अलवर"], "Ajmer", "अजमेर", 0.9)


def test_english_moderate_confidence_still_resolves():
    _t(["186792 नटिनी", "186793 कुछ"], "Nadini", "नटिनी")          # override or fuzzy
    _t(["11035 अजयसर (चालु)", "11036 xyz"], "Ajaysar", "अजयसर")


def test_devanagari_query_exact():
    m = resolve(["01 अजमेर", "02 अलवर"], "अजमेर")
    assert m.text == "01 अजमेर" and m.method == "exact" and m.score == 1.0


def test_code_prefix_is_ignored():
    m = resolve(["146 आगरा", "147 अलीगढ़"], "आगरा")
    assert m.text == "146 आगरा"


def test_override_resolves_hard_name():
    # Forbesganj <-> फारबिसगंज is beyond fuzzy; the override table carries it
    from gps_engine.config import NAMING_OVERRIDES
    m = resolve(["02 फारबिसगंज", "01 अररिया"], "Forbesganj", overrides=NAMING_OVERRIDES)
    assert m is not None and "फारबिसगंज" in m.text and m.method == "override"


def test_unresolvable_returns_none():
    assert resolve(["01 अजमेर", "02 अलवर"], "Chennai") is None
    assert resolve([], "Anything") is None
    assert resolve(["--Select--"], "Anything") is None


def test_placeholder_options_skipped():
    m = resolve(["--Select--", "----Select----", "05 अकोला"], "Akola")
    assert m.text == "05 अकोला"
