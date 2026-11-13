"""
Dependency-free text utilities: sentence segmentation, case matching and
verb conjugation.

Nothing here needs NLTK data, model downloads or network access, so the whole
package imports and runs on a fresh machine.
"""

from __future__ import annotations

import re

# Abbreviations whose trailing period must never end a sentence.
_ABBREVIATIONS = (
    "e.g", "i.e", "et al", "etc", "vs", "cf", "viz", "approx", "ca", "resp",
    "Fig", "Figs", "Tab", "Eq", "Eqs", "Ref", "Refs", "Sec", "Ch", "Vol", "No",
    "Nos", "pp", "p", "ed", "eds", "Dr", "Mr", "Mrs", "Ms", "Prof", "Sr", "Jr",
    "St", "Mt", "Inc", "Ltd", "Co", "Corp", "Jan", "Feb", "Mar", "Apr", "Jun",
    "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec", "U.S", "U.K", "a.m", "p.m",
    "min", "max", "avg", "est", "dept", "univ", "al",
)
_ABBR_RE = re.compile(
    r"(?<![\w.])(" + "|".join(re.escape(a) for a in sorted(_ABBREVIATIONS, key=len, reverse=True)) + r")\.",
    re.IGNORECASE,
)
# Single capital initials such as "J. Smith".
_INITIAL_RE = re.compile(r"(?<![\w.])([A-Z])\.(?=\s+[A-Z])")
_SENT_BOUNDARY = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+(?=[\"'“‘(\[]?[A-Z0-9_<])")
_MASK = "⁣"  # invisible separator used as a temporary period mask


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences, keeping abbreviations, initials and decimals intact."""
    if not text or not text.strip():
        return []
    masked = _ABBR_RE.sub(lambda m: m.group(1) + _MASK, text)
    masked = _INITIAL_RE.sub(lambda m: m.group(1) + _MASK, masked)
    parts = _SENT_BOUNDARY.split(masked.strip())
    return [p.replace(_MASK, ".").strip() for p in parts if p and p.strip()]


_ABBR_SET = {a.lower() for a in _ABBREVIATIONS}


def ends_with_abbreviation(prefix: str) -> bool:
    """True if `prefix` (text up to and including a period) ends with a known abbreviation or initial."""
    m = re.search(r"([A-Za-z][A-Za-z.]*)\.$", prefix)
    if not m:
        return False
    tok = m.group(1).lower()
    return tok in _ABBR_SET or len(tok) == 1


def capitalize_sentences(text: str) -> str:
    """Upper-case a lower-case word that follows sentence-ending punctuation (abbreviation-aware)."""

    def _sub(m: re.Match[str]) -> str:
        if m.group(1) == "." and ends_with_abbreviation(m.string[: m.start() + 1]):
            return m.group(0)
        word = m.group(3)
        if word in ("e", "i") and m.string[m.end():m.end() + 1] == ".":
            return m.group(0)  # e.g. / i.e.
        return m.group(1) + m.group(2) + word[0].upper() + word[1:]

    text = re.sub(r"([.!?])([\"'”’)\]]*\s+)([a-z][a-z]*)\b", _sub, text)
    return re.sub(r"^(\s*[\"'“‘(\[]*)([a-z][a-z]*)\b", lambda m: m.group(1) + m.group(2)[0].upper() + m.group(2)[1:], text)


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z'’-]*", text or "")


def match_case(src: str, repl: str) -> str:
    """Return `repl` in the capitalisation style of `src`."""
    if not repl:
        return repl
    if src.isupper() and len(src) > 1:
        return repl.upper()
    if src[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


# ---------------------------------------------------------------------------
# Verb conjugation (enough for the substitution tables; irregulars listed)
# ---------------------------------------------------------------------------
_IRREGULAR = {
    # base: (3sg, past, past participle, ing)
    "be": ("is", "was", "been", "being"),
    "have": ("has", "had", "had", "having"),
    "do": ("does", "did", "done", "doing"),
    "go": ("goes", "went", "gone", "going"),
    "get": ("gets", "got", "got", "getting"),
    "make": ("makes", "made", "made", "making"),
    "build": ("builds", "built", "built", "building"),
    "lead": ("leads", "led", "led", "leading"),
    "show": ("shows", "showed", "shown", "showing"),
    "keep": ("keeps", "kept", "kept", "keeping"),
    "begin": ("begins", "began", "begun", "beginning"),
    "find": ("finds", "found", "found", "finding"),
    "give": ("gives", "gave", "given", "giving"),
    "take": ("takes", "took", "taken", "taking"),
    "set": ("sets", "set", "set", "setting"),
    "put": ("puts", "put", "put", "putting"),
    "dig": ("digs", "dug", "dug", "digging"),
    "win": ("wins", "won", "won", "winning"),
    "grow": ("grows", "grew", "grown", "growing"),
    "run": ("runs", "ran", "run", "running"),
    "say": ("says", "said", "said", "saying"),
    "see": ("sees", "saw", "seen", "seeing"),
    "use": ("uses", "used", "used", "using"),
}
_VOWELS = "aeiou"


def _double_final(base: str) -> bool:
    """CVC one-syllable-ish verbs double the final consonant (stop -> stopping)."""
    if len(base) < 3 or base[-1] in "wxy" + _VOWELS:
        return False
    if base[-2] not in _VOWELS or base[-3] in _VOWELS:
        return False
    # Only short stems: avoid 'develop' -> 'developping'.
    return len(re.findall(r"[aeiouy]+", base)) == 1


def conjugate(base: str) -> dict[str, str]:
    """Return {'base','3sg','past','pp','ing'} for a (possibly multi-word) verb phrase."""
    head, _, rest = base.partition(" ")
    tail = f" {rest}" if rest else ""
    if head in _IRREGULAR:
        s3, past, pp, ing = _IRREGULAR[head]
    else:
        if head.endswith(("s", "sh", "ch", "x", "z", "o")):
            s3 = head + "es"
        elif head.endswith("y") and head[-2:-1] not in _VOWELS:
            s3 = head[:-1] + "ies"
        else:
            s3 = head + "s"
        if head.endswith("e"):
            past = head + "d"
            ing = head[:-1] + "ing" if not head.endswith(("ee", "ye", "oe")) else head + "ing"
        elif head.endswith("y") and head[-2:-1] not in _VOWELS:
            past = head[:-1] + "ied"
            ing = head + "ing"
        elif _double_final(head):
            past = head + head[-1] + "ed"
            ing = head + head[-1] + "ing"
        else:
            past = head + "ed"
            ing = head + "ing"
        pp = past
    return {
        "base": head + tail,
        "3sg": s3 + tail,
        "past": past + tail,
        "pp": pp + tail,
        "ing": ing + tail,
    }


def ing_to_3sg(verb_ing: str) -> str:
    """Convert a present participle to third-person singular ("improving" -> "improves")."""
    v = verb_ing.lower()
    if not v.endswith("ing") or len(v) < 5:
        return verb_ing + "s"
    stem = v[:-3]
    irregular = {"having": "has", "being": "is", "doing": "does", "going": "goes",
                 "underlying": "underlies", "lying": "lies", "dying": "dies", "tying": "ties"}
    if v in irregular:
        res = irregular[v]
    elif stem.endswith("y") and stem[-2:-1] not in _VOWELS:
        res = stem[:-1] + "ies"  # amplifying -> amplifies
    elif len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "lsz":
        res = stem[:-1] + "s"  # stopping -> stops
    elif stem.endswith(("sh", "ch", "x", "ss", "zz")):
        res = stem + "es"  # pushing -> pushes
    elif _needs_e(stem):
        res = stem + "es"  # creating -> creates, reducing -> reduces
    else:
        res = stem + "s"
    return match_case(verb_ing, res)


# Stems that drop a silent 'e' before -ing. A short explicit list beats heuristics
# for the common academic verbs; everything else falls back to the heuristic.
_E_STEMS = {
    "creat", "generat", "produc", "reduc", "increas", "improv", "enabl", "provid",
    "promot", "ensur", "accelerat", "indicat", "demonstrat", "facilitat", "giv",
    "tak", "mak", "chang", "caus", "mitigat", "optimiz", "optimis", "influenc",
    "requir", "elevat", "driv", "reshap", "pav", "serv", "rais", "achiev", "involv",
    "us", "compris", "advanc", "balanc", "enhanc", "includ", "determin", "examin",
    "measur", "compar", "relat", "integrat", "operat", "regulat", "simulat",
    "calculat", "estimat", "evaluat", "illustrat", "investigat", "separat",
    "stabiliz", "minimiz", "maximiz", "organiz", "recogniz", "emphasiz",
    "synthesiz", "characteriz", "analyz", "catalyz", "hydrolyz", "decompos",
    "dissolv", "solv", "resolv", "observ", "conserv", "preserv", "deriv",
    "describ", "prescrib", "subscrib", "sav", "mov", "remov", "prov", "approv",
    "receiv", "perceiv", "deceiv", "continu", "argu", "issu", "valu", "pursu",
    "rul", "schedul", "scal", "handl", "enrol", "coupl", "doubl", "tripl",
    "sampl", "settl", "titl", "cycl", "recycl", "bubbl", "assembl", "resembl",
    "compil", "combin", "defin", "refin", "outlin", "declin", "shap", "escap",
    "tim", "fram", "nam", "sens", "licens", "respons", "dispens", "expos",
    "propos", "suppos", "impos", "oppos", "purpos", "clos", "pleas", "releas",
    "chas", "purchas", "phras", "excit", "invit", "writ", "unit", "quot",
    "not", "vot", "devot", "rotat", "translat", "accumulat", "manipulat",
    "fac", "plac", "replac", "trac", "surfac", "pac", "introduc",
    "sourc", "forc", "enforc", "reinforc", "divid", "decid", "guid", "slid",
    "glid", "abid", "hous", "rous", "fus", "abus", "excus", "refus", "confus",
    "diffus", "infus", "amus", "paus", "abs", "arrang",
    "challeng", "emerg", "merg", "urg", "charg", "discharg", "enlarg",
    "manag", "engag", "encourag", "damag", "imag", "averag", "leverag",
}


def _needs_e(stem: str) -> bool:
    return stem in _E_STEMS


__all__ = ["capitalize_sentences", "ends_with_abbreviation", "split_sentences", "words", "match_case", "conjugate", "ing_to_3sg"]
