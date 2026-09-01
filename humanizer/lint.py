"""
Grammar, Readability, and Integrity Linter.
Catches AI residual tells, broken punctuation/spaces, and fabricated citations.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

_FFFD = "\uFFFD"
_AI_TELLS = re.compile(
    r"\b(delve|leverage|foster|harness|transformative|revolutioniz\w+|pivotal|robust|"
    r"seamless|nuanced|multifaceted|holistic|tapestry|underscore|showcase|testament|"
    r"comprehensive|facilitate|utiliz\w+|moreover|furthermore|notably|paramount|beacon|"
    r"ever-evolving|in today's world|rich tapestry)\b",
    re.IGNORECASE,
)
_DUP_WORD = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE)
_A_AN = re.compile(r"\b([Aa])\s+([aeiouAEIOU]\w+)")
_AN_A = re.compile(r"\b([Aa]n)\s+([bcdfgjklmnpqrstvwxz]\w+)", re.IGNORECASE)
_SPACE_PUNC = re.compile(r"\s+[,.;:!?]")
_DUP_PUNC = re.compile(r"([.!?]{2,}|,{2,})")
_LIST_SPLIT = re.compile(r",\s+\w+\.\s+(And|Or|Nor)\s")
_CITATION = re.compile(r"\[\d+(?:[,\-]\s*\d+)*\]")

_A_OK_VOWEL = re.compile(r"^(uni|use|user|usu|euro|eu|one|once|ubiq|unique|unicorn|unit|univ|ufo)", re.I)
_AN_OK_CONS = re.compile(r"^(hour|honest|honou?r|heir|x-?ray|mri|fbi)", re.I)


def lint_text(text: str, src: Optional[str] = None) -> list[tuple[str, str]]:
    """Return [(severity, message)] for `text`. If `src` given, flag fabricated citations."""
    issues: list[tuple[str, str]] = []

    if _FFFD in text:
        issues.append(("error", f"contains U+FFFD replacement char x{text.count(_FFFD)}"))
    if "  " in text:
        issues.append(("warn", "double space present"))
    for m in _DUP_WORD.finditer(text):
        issues.append(("warn", f"doubled word: '{m.group(0)}'"))
    for m in _A_AN.finditer(text):
        if not _A_OK_VOWEL.match(m.group(2)):
            issues.append(("error", f"a/an: '{m.group(0)}' should be 'an {m.group(2)}'"))
    for m in _AN_A.finditer(text):
        if not _AN_OK_CONS.match(m.group(2)):
            issues.append(("error", f"a/an: '{m.group(0)}' should be 'a {m.group(2)}'"))
    if _SPACE_PUNC.search(text):
        issues.append(("error", "space before punctuation"))
    if _DUP_PUNC.search(text):
        issues.append(("error", "doubled punctuation"))
    if _LIST_SPLIT.search(text):
        issues.append(("error", "list appears split into a fragment ('A, B. And C')"))
    tells = _AI_TELLS.findall(text)
    if tells:
        issues.append(("warn", f"AI-tell words survive: {sorted(set(t.lower() for t in tells))}"))

    if src is not None:
        src_cites = set(_CITATION.findall(src))
        fabricated = [c for c in _CITATION.findall(text) if c not in src_cites]
        if fabricated:
            issues.append(("error", f"fabricated citation(s): {fabricated}"))
        dropped = [c for c in src_cites if c not in set(_CITATION.findall(text))]
        if dropped:
            issues.append(("error", f"dropped source citation(s): {dropped}"))

    return issues


def is_clean(text: str, src: Optional[str] = None) -> bool:
    return not any(sev == "error" for sev, _ in lint_text(text, src))


def _syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and n > 1:
        n -= 1
    return max(1, n)


def readability(text: str) -> dict:
    """Flesch Reading Ease + sentence-length variance (burstiness)."""
    sents = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    words = re.findall(r"[A-Za-z']+", text)
    if not sents or not words:
        return {"flesch": None, "avg_sentence_len": 0.0, "burstiness": 0.0}
    syl = sum(_syllables(w) for w in words)
    nw, ns = len(words), len(sents)
    flesch = 206.835 - 1.015 * (nw / ns) - 84.6 * (syl / nw)
    lens = [len(re.findall(r"[A-Za-z']+", s)) for s in sents]
    mean = sum(lens) / len(lens)
    var = sum((l - mean) ** 2 for l in lens) / len(lens)
    return {
        "flesch": round(flesch, 1),
        "avg_sentence_len": round(mean, 1),
        "burstiness": round(var ** 0.5, 1),
    }
