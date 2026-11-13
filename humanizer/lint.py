"""
Grammar, readability and integrity linter.

Catches residual AI tells, broken punctuation/spacing, wrong articles, leaked
placeholders, and — when the source text is supplied — fabricated or dropped
citations, URLs and numbers.
"""

from __future__ import annotations

import re
from typing import Optional

from humanizer.patterns import find_tells
from humanizer.text import split_sentences, words

_FFFD = "�"
_DUP_WORD = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE)
_DUP_OK = {"that", "had", "very", "bye", "no", "so", "ha", "is"}
_ARTICLE = re.compile(r"\b(a|an)\s+([A-Za-z][\w'-]*)", re.IGNORECASE)
_A_EXCEPT = re.compile(r"^(?:uni(?!n|mp|nt|mm)|us[eu]|uti|ura|ure|uri|uro|eu|one\b|once|ubiq|ufo|uk|uto|ute|ewe)", re.IGNORECASE)
_AN_EXCEPT = re.compile(r"^(?:hour|honest|honou?r|heir|herb\b)", re.IGNORECASE)
_SPACE_PUNC = re.compile(r"[ \t]+[,.;:!?](?!\d)")
_DUP_PUNC = re.compile(r"(?<!\.)\.\.(?!\.)|,{2,}|[!?]{3,}")
_LIST_SPLIT = re.compile(r",\s+\w+\.\s+(And|Or|Nor)\s")
_DUMMY_CITES = re.compile(r"\[(?:citation(?: needed)?|ref|reference|source|cite|insert\s+citation)\]", re.IGNORECASE)
_NUM_CITE = re.compile(r"\[\d+(?:\s*[,\-–]\s*\d+)*\]")
_AUTHOR_CITE = re.compile(r"\([A-Z][A-Za-z'’\-]+(?: et al\.?| and [A-Z][A-Za-z'’\-]+| & [A-Z][A-Za-z'’\-]+)?,? (?:19|20)\d{2}[a-z]?\)")
_URL = re.compile(r"https?://[^\s)\]]+|www\.[^\s)\]]+")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?![\w])")


def _article_wrong(art: str, word: str) -> bool:
    if any(c.isdigit() for c in word) or (word.isupper() and len(word) > 1) or word.startswith("_"):
        return False
    want_an = (word[0].lower() in "aeiou" and not _A_EXCEPT.match(word)) or bool(_AN_EXCEPT.match(word))
    return want_an != (art.lower() == "an")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def lint_text(text: str, src: Optional[str] = None) -> list[tuple[str, str]]:
    """Return [(severity, message)]. If `src` is given, also check fidelity against it."""
    issues: list[tuple[str, str]] = []
    if not text:
        return issues

    if _FFFD in text:
        issues.append(("error", f"contains U+FFFD replacement char x{text.count(_FFFD)}"))
    if re.search(r"[^\s] {2,}[^\s]", text):
        issues.append(("warn", "double space present"))
    if "__LOCK_" in text or "__RUN_LOCKED_" in text or re.search(r"</?(?:b|i|bi) id=", text):
        issues.append(("error", "unexpanded placeholder token(s) present in text"))
    seen_dups = set()
    for m in _DUP_WORD.finditer(text):
        w = m.group(1).lower()
        if w not in _DUP_OK and not w.isdigit() and w not in seen_dups:
            seen_dups.add(w)
            issues.append(("warn", f"doubled word: '{m.group(0)}'"))
    for m in _ARTICLE.finditer(text):
        if _article_wrong(m.group(1), m.group(2)):
            fix = "an" if m.group(1).lower() == "a" else "a"
            issues.append(("error", f"a/an: '{m.group(0)}' should be '{fix} {m.group(2)}'"))
    if _SPACE_PUNC.search(text):
        issues.append(("error", "space before punctuation"))
    if _DUP_PUNC.search(text):
        issues.append(("error", "doubled punctuation"))
    if _LIST_SPLIT.search(text):
        issues.append(("error", "list appears split into a fragment ('A, B. And C')"))
    dummy = _DUMMY_CITES.findall(text)
    if dummy:
        issues.append(("error", f"dummy placeholder citation(s) found: {sorted(set(dummy))}"))

    tells = find_tells(text)
    vocab = sorted({t["text"].lower().strip() for t in tells if t["category"] != "dash"})
    if vocab:
        issues.append(("warn", f"AI-tell words survive: {vocab[:12]}"))

    if src is not None:
        fab_urls = set(_URL.findall(text)) - set(_URL.findall(src))
        if fab_urls:
            issues.append(("error", f"fabricated URL(s): {sorted(fab_urls)}"))
        src_cites = set(_NUM_CITE.findall(src)) | set(_AUTHOR_CITE.findall(src))
        out_cites = set(_NUM_CITE.findall(text)) | set(_AUTHOR_CITE.findall(text))
        fabricated = sorted(out_cites - src_cites)
        if fabricated:
            issues.append(("error", f"fabricated citation(s): {fabricated}"))
        dropped = sorted(src_cites - out_cites)
        if dropped:
            issues.append(("error", f"dropped source citation(s): {dropped}"))
        missing_nums = sorted(_numbers(src) - _numbers(text))
        if missing_nums:
            issues.append(("error", f"number(s) changed or dropped: {missing_nums[:10]}"))
        new_nums = sorted(_numbers(text) - _numbers(src))
        if new_nums:
            issues.append(("warn", f"new number(s) introduced: {new_nums[:10]}"))

    return issues


def is_clean(text: str, src: Optional[str] = None) -> bool:
    return not any(sev == "error" for sev, _ in lint_text(text, src))


def _syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def readability(text: str) -> dict:
    """Flesch Reading Ease, average sentence length and sentence-length spread."""
    sents = split_sentences(text or "")
    ws = words(text or "")
    if not sents or not ws:
        return {"flesch": None, "grade": None, "avg_sentence_len": 0.0, "burstiness": 0.0}
    syl = sum(_syllables(w) for w in ws)
    nw, ns = len(ws), len(sents)
    flesch = 206.835 - 1.015 * (nw / ns) - 84.6 * (syl / nw)
    grade = 0.39 * (nw / ns) + 11.8 * (syl / nw) - 15.59
    lens = [len(words(s)) for s in sents]
    mean = sum(lens) / len(lens)
    var = sum((x - mean) ** 2 for x in lens) / len(lens)
    return {
        "flesch": round(flesch, 1),
        "grade": round(max(0.0, grade), 1),
        "avg_sentence_len": round(mean, 1),
        "burstiness": round(var ** 0.5, 1),
    }


__all__ = ["lint_text", "is_clean", "readability"]
