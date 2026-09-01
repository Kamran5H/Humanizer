"""
Optional NLP enhancements for the humanizer. Every capability here is GUARDED: if the
backing library is absent the function degrades to a safe no-op / fallback, so the app
launches and runs exactly as before. Nothing here is a hard dependency.

Curated from a larger candidate list. What earned a place, and why:

  • ftfy        — fixes mojibake / broken encoding far better than hand-rolled regex.
                  Drives `fix_encoding`, used by the humanizer's unicode normaliser.
  • scikit-learn— TF-IDF cosine for a MEANING-DRIFT gate: reject a rewrite that quietly
                  dropped or compressed content ("find, fix, and handle us"). Drives
                  `is_faithful` / `meaning_similarity`.
  • spaCy       — abbreviation-safe sentence segmentation + dependency parse for robust
                  serial-list detection. Drives `sentences` and `is_serial_list_comma`.

Deliberately NOT used (kept the app lean, per the quality bar):
  • nlpaug      — random insert/delete/synonym augmentation produces wrong-sense, broken
                  text; it is exactly the failure mode we removed. Conflicts with "zero
                  wrong-sense swaps".
  • langchain / langgraph / llama-index / instructor / marvin — framework bloat for a
                  linear pipeline; we already do structured JSON + a detector-gated loop
                  in plain code ("as deterministic as possible").
  • stanza / benepar — heavy torch parsers; spaCy's small model covers what we need.
  • textblob / clean-text — redundant with spaCy + ftfy.
  • contractions— its fix() EXPANDS contractions (don't→do not); we want the opposite
                  for a human voice, so its direction is wrong for us.
  • litellm     — our zero-dep OpenAI-compatible adapter already covers Groq/OpenRouter/
                  Ollama; not worth a dependency.
"""

from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# ftfy — encoding repair
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _ftfy():
    try:
        import ftfy
        return ftfy
    except Exception:
        return None


def fix_encoding(text: str) -> str:
    """Repair mojibake / broken unicode with ftfy if available; else return as-is."""
    f = _ftfy()
    if f is None:
        return text
    try:
        return f.fix_text(text)
    except Exception as e:
        logger.warning(f"ftfy fix_text failed: {e}")
        return text


def has_ftfy() -> bool:
    return _ftfy() is not None


# --------------------------------------------------------------------------- #
# scikit-learn — meaning-drift gate (TF-IDF cosine)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _sklearn_bits():
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        return TfidfVectorizer, cosine_similarity
    except Exception:
        return None


def meaning_similarity(a: str, b: str) -> float | None:
    """TF-IDF cosine similarity of two texts in 0..1, or None if sklearn is absent.

    Word-overlap based, so a heavy paraphrase legitimately scores lower — this is for
    catching SEVERE drift / dropped content, not for measuring paraphrase quality.
    """
    bits = _sklearn_bits()
    if bits is None or not a.strip() or not b.strip():
        return None
    TfidfVectorizer, cosine_similarity = bits
    try:
        v = TfidfVectorizer(stop_words="english").fit_transform([a, b])
        return float(cosine_similarity(v[0], v[1])[0][0])
    except Exception as e:
        logger.warning(f"meaning_similarity failed: {e}")
        return None


def is_faithful(src: str, out: str, min_cosine: float = 0.30,
                min_len_ratio: float = 0.55) -> bool:
    """True unless `out` looks like it dropped/compressed `src`'s content.

    Two cheap signals: severe length collapse, or near-zero word-overlap cosine. Lenient
    by design — a normal paraphrase passes; only gutted/off-topic rewrites fail. When
    sklearn is absent it falls back to the length check alone.
    """
    if not out.strip():
        return False
    src_w, out_w = len(src.split()), len(out.split())
    if src_w >= 25 and out_w < src_w * min_len_ratio:
        return False
    sim = meaning_similarity(src, out)
    if sim is not None and sim < min_cosine:
        return False
    return True


# --------------------------------------------------------------------------- #
# spaCy — robust sentence segmentation + serial-list detection
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _spacy_nlp():
    try:
        import spacy
        try:
            nlp = spacy.load("en_core_web_sm", disable=["ner", "lemmatizer"])
        except Exception:
            return None
        return nlp
    except Exception:
        return None


def sentences(text: str) -> list[str] | None:
    """Abbreviation-safe sentence split via spaCy, or None if the model is unavailable
    (caller should fall back to nltk). spaCy keeps 'e.g.' / 'Dr.' intact better than a
    regex split."""
    nlp = _spacy_nlp()
    if nlp is None:
        return None
    try:
        return [s.text.strip() for s in nlp(text).sents if s.text.strip()]
    except Exception as e:
        logger.warning(f"spaCy sentence split failed: {e}")
        return None


def has_spacy() -> bool:
    return _spacy_nlp() is not None


def capabilities() -> dict:
    """Report which optional enhancements are live (for diagnostics / GUI status)."""
    return {"ftfy": has_ftfy(),
            "sklearn_meaning_gate": _sklearn_bits() is not None,
            "spacy": has_spacy()}


if __name__ == "__main__":
    print("nlp_enhance capabilities:", capabilities())
    print("encoding fix:", repr(fix_encoding("Its a testâ€”ok")))
    print("similarity (same):", meaning_similarity("the cat sat on the mat",
                                                   "the cat sat on the mat"))
    print("similarity (drift):", meaning_similarity(
        "AI diagnoses, treats, and manages patient care across hospitals",
        "find fix handle us"))
    print("faithful (drift):", is_faithful(
        "AI diagnoses, treats, and manages patient care across many hospitals worldwide today",
        "find fix handle us"))
