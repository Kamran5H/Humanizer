"""
Small, dependency-free NLP helpers: encoding repair and a meaning-drift gate.

``ftfy`` is used when installed for best-in-class mojibake repair; otherwise a
built-in table fixes the common UTF-8-read-as-cp1252 sequences.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache

_MOJIBAKE = {
    "â€™": "’", "â€˜": "‘", "â€œ": "“", "â€\x9d": "”", "â€�": "”", "â€”": "—", "â€“": "–",
    "â€¦": "…", "Â ": " ", "Â©": "©", "Â°": "°", "Â±": "±", "Ã©": "é", "Ã¨": "è",
    "Ã¶": "ö", "Ã¼": "ü", "Ã¤": "ä", "Ã±": "ñ", "Ã§": "ç", "Î¼": "μ", "â€¢": "•",
}
_STOP = set("""a an the and or but if of to in on at for by with from as is are was were be been being it its
this that these those there their they them we our you your he she his her i me my not no so than then
which who whom whose what when where why how can could may might will would shall should do does did
has have had also into over under about after before between through during such more most very""".split())


@lru_cache(maxsize=1)
def _ftfy():
    try:
        import ftfy  # type: ignore
        return ftfy
    except Exception:
        return None


def fix_encoding(text: str) -> str:
    """Repair mojibake / broken unicode."""
    if not text:
        return text
    f = _ftfy()
    if f is not None:
        try:
            return f.fix_text(text, normalization="NFC")
        except Exception:
            pass
    if "Ã" in text or "â€" in text or "Â" in text or "Î" in text:
        for bad, good in _MOJIBAKE.items():
            text = text.replace(bad, good)
    return text


def _bag(text: str) -> Counter:
    toks = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z'-]+|\d+(?:\.\d+)?", text)]
    # crude stemming so "reduces"/"reducing"/"reduced" overlap
    out = []
    for t in toks:
        if t in _STOP:
            continue
        for suf in ("ing", "ed", "es", "s", "ly"):
            if len(t) > len(suf) + 3 and t.endswith(suf):
                t = t[: -len(suf)]
                break
        out.append(t)
    return Counter(out)


def meaning_similarity(a: str, b: str) -> float | None:
    """Cosine similarity of content-word bags (0..1). None for empty input."""
    if not a or not b or not a.strip() or not b.strip():
        return None
    ca, cb = _bag(a), _bag(b)
    if not ca or not cb:
        return None
    dot = sum(ca[k] * cb[k] for k in ca.keys() & cb.keys())
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    return dot / (na * nb) if na and nb else 0.0


def is_faithful(src: str, out: str, min_cosine: float = 0.25, min_len_ratio: float = 0.55) -> bool:
    """False when `out` looks gutted or off-topic relative to `src` (lenient for paraphrases)."""
    if not out or not out.strip():
        return False
    sw, ow = len(src.split()), len(out.split())
    if sw >= 25 and ow < sw * min_len_ratio:
        return False
    sim = meaning_similarity(src, out)
    if sim is not None and sw >= 12 and sim < min_cosine:
        return False
    return True


def capabilities() -> dict:
    return {"ftfy": _ftfy() is not None, "meaning_gate": True}


__all__ = ["fix_encoding", "meaning_similarity", "is_faithful", "capabilities"]
