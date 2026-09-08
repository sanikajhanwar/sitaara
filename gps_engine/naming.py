"""
GPS Engine — Name Resolution Layer
=================================
State cadastral portals list districts / tehsils / villages in Devanagari (or Marathi /
Gujarati), usually with a numeric code prefix: "188 गोरखपुर", "05 अकोला".

`resolve()` takes the live `<option>` texts + the English (or Devanagari) name the caller
asked for, and returns the best-matching option text with a confidence score, so callers
never hard-code a transliteration table.

Strategy, best score wins:
  1. exact / substring match on the code-stripped option (works when caller passes Devanagari)
  2. transliterate each option Devanagari -> Latin (+ a schwa-deleted variant) and fuzzy-match
     against the caller's query with rapidfuzz
  3. an optional per-portal override map for known-hard cases

No network, no Playwright — pure text. Unit-testable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz
from indic_transliteration import sanscript
from indic_transliteration.sanscript import transliterate

_CODE_PREFIX = re.compile(r"^\s*[0-9]+\s+")
_NON_DEVANAGARI = re.compile(r"[^ऀ-ॿ]")
_HAS_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

DEFAULT_THRESHOLD = 0.72
LOW_CONFIDENCE = 0.85   # below this -> attach a warning, still accept above threshold


@dataclass
class Match:
    text: str            # the exact option text to select ("188 गोरखपुर")
    score: float         # 0..1
    method: str          # exact | substring | translit | override
    query: str

    @property
    def low_confidence(self) -> bool:
        return self.score < LOW_CONFIDENCE


def _strip_code(option: str) -> str:
    return _CODE_PREFIX.sub("", option).strip()


def _strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _schwa_delete(latin: str) -> str:
    """
    Crude inherent-'a' removal: Devanagari transliteration renders हिन्दी as 'hindI' but
    each bare consonant carries an 'a' (गोरखपुर -> 'gorakhapura'). Drop a short 'a' that
    sits between two consonants or is word-final, which pulls 'gorakhapura' -> 'gorkhpur'.
    """
    vowels = "aeiouāīūṛeo"
    out = []
    for i, ch in enumerate(latin):
        if ch == "a":
            prev = latin[i - 1] if i > 0 else ""
            nxt = latin[i + 1] if i + 1 < len(latin) else ""
            if prev and prev not in vowels and (nxt == "" or nxt not in vowels):
                # keep the 'a' if dropping it would collide two identical consonants awkwardly
                continue
        out.append(ch)
    return "".join(out)


_PHONETIC = [
    ("ph", "f"), ("kh", "k"), ("gh", "g"), ("th", "t"), ("dh", "d"),
    ("bh", "b"), ("ch", "c"), ("sh", "s"), ("w", "v"), ("z", "j"), ("q", "k"),
]


def _phonetic_fold(s: str) -> str:
    s = s.lower()
    for a, b in _PHONETIC:
        s = s.replace(a, b)
    return re.sub(r"[^a-z]", "", s)


def _latin_forms(devanagari: str) -> List[str]:
    core = _strip_code(devanagari)
    if not _HAS_DEVANAGARI.search(core):
        base = re.sub(r"[^a-z]", "", core.lower())
        return [f for f in {base, _phonetic_fold(base)} if f]
    iast = transliterate(core, sanscript.DEVANAGARI, sanscript.IAST)
    base = re.sub(r"[^a-z]", "", _strip_diacritics(iast).lower())
    forms = {base, _schwa_delete(base), _phonetic_fold(base), _phonetic_fold(_schwa_delete(base))}
    return [f for f in forms if f]


def _best_fuzzy(query_latin: str, option_latin_forms: List[str]) -> float:
    if not query_latin or not option_latin_forms:
        return 0.0
    return max(
        max(
            fuzz.WRatio(query_latin, f),
            fuzz.ratio(query_latin, f),
            fuzz.token_sort_ratio(query_latin, f),
        )
        for f in option_latin_forms
    ) / 100.0


def resolve(
    options: List[str],
    query: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    overrides: Optional[Dict[str, str]] = None,
) -> Optional[Match]:
    """
    Args:
        options:   live <option> texts, e.g. ["--Select--", "05 अकोला", "07 अमरावती", ...]
        query:     name the caller wants ("Akola" or "अकोला")
        overrides: {english_lower: devanagari_core} for known-hard cases
    Returns best Match or None.
    """
    query = (query or "").strip()
    if not query:
        return None

    real = [o for o in options if o and _strip_code(o) not in ("", "--Select--", "----Select----", "Select")]
    if not real:
        return None

    q_low = query.lower()
    q_is_dev = bool(_HAS_DEVANAGARI.search(query))

    overrides = {k.lower(): v for k, v in (overrides or {}).items()}
    if q_low in overrides:
        target = overrides[q_low].lower()
        for o in real:
            if _strip_code(o).lower() == target or target in _strip_code(o).lower():
                return Match(o, 1.0, "override", query)

    # 1. exact / substring on the code-stripped option
    for o in real:
        core = _strip_code(o).lower()
        if core == q_low:
            return Match(o, 1.0, "exact", query)
    for o in real:
        core = _strip_code(o).lower()
        if q_low and (q_low in core or core in q_low) and abs(len(core) - len(q_low)) <= 4:
            return Match(o, 0.93, "substring", query)

    # 2. transliterate + fuzzy
    if q_is_dev:
        q_forms = _latin_forms(query)
    else:
        qb = re.sub(r"[^a-z]", "", q_low)
        q_forms = [qb, _schwa_delete(qb), _phonetic_fold(qb)]
    q_forms = [f for f in dict.fromkeys(q_forms) if f]

    best: Optional[Match] = None
    for o in real:
        o_forms = _latin_forms(o)
        score = max(_best_fuzzy(qf, o_forms) for qf in q_forms) if q_forms else 0.0
        if best is None or score > best.score:
            best = Match(o, round(score, 3), "translit", query)

    if best and best.score >= threshold:
        return best
    return None


def resolve_or_raise(options: List[str], query: str, level_name: str = "level", **kw) -> Match:
    m = resolve(options, query, **kw)
    if m is None:
        raise LookupError(f"could not resolve {level_name} '{query}' among {len(options)} options")
    return m
