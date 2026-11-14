"""
Local, explainable AI-likelihood scorer.

The score is an ensemble of signals that commercial detectors and the
Wikipedia "Signs of AI writing" guide both rely on:

* **Burstiness** — humans vary sentence length a lot; LLMs write evenly.
* **AI vocabulary & stock phrases** — weighted hits from :mod:`humanizer.patterns`.
* **Formulaic structure** — transition openers, shallow ``-ing`` tails,
  rule-of-three lists, em-dash asides, ``not only ... but also``.
* **Uniformity** — repeated sentence openers and a narrow mid-length band.
* **Perplexity** (optional) — token predictability under GPT-2 when
  ``torch`` + ``transformers`` are installed. Without them the signal is
  skipped rather than faked, and the other weights are renormalised.

Everything runs offline. Scores are an *estimate*; they are not a guarantee
of what any third-party detector will say.
"""

from __future__ import annotations

import math
import re
import statistics
from functools import lru_cache
from typing import Optional

from humanizer.patterns import find_tells
from humanizer.text import split_sentences, words

_MODEL_NAME = "distilgpt2"
_TORCH_STATE: dict[str, object] = {}

_TRANSITION_START = re.compile(
    r"^[\"'“(]*(?:Furthermore|Moreover|Additionally|In addition|Notably|Importantly|Consequently|"
    r"Ultimately|Indeed|Interestingly|Crucially|Overall|In conclusion|In summary|Thus|Therefore|"
    r"Hence|However|Nevertheless|Nonetheless|Similarly|Likewise|Specifically|Essentially)\b,?"
)
_TAIL = re.compile(r",\s+(?:thereby\s+|thus\s+)?[a-z]+ing\b[^,.;!?]*[.!?]?$")
_TRIAD = re.compile(r"\b[\w-]+(?: [\w-]+){0,3}, [\w-]+(?: [\w-]+){0,3},? (?:and|or) [\w-]+")
_DASH = re.compile(r"—|\s--\s")
_CONTRACTION = re.compile(r"\b\w+['’](?:s|t|re|ve|ll|d|m)\b", re.IGNORECASE)

# Signal weights (sum to 1 when perplexity is available).
_W = {"burstiness": 0.28, "vocabulary": 0.30, "structure": 0.20, "uniformity": 0.10, "perplexity": 0.12}

SCORE_EXPLANATION = ("Local writing-pattern index (0–100), not a probability or percentage of AI-written text. "
                     "Not calibrated against Turnitin; scores from different systems are not directly comparable. "
                     "A low score does not establish human authorship.")


# ---------------------------------------------------------------------------
# Optional neural perplexity
# ---------------------------------------------------------------------------
def _load_model():
    if "loaded" in _TORCH_STATE:
        return _TORCH_STATE.get("bundle")
    _TORCH_STATE["loaded"] = True
    _TORCH_STATE["bundle"] = None
    try:
        import torch  # type: ignore
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore

        tok = AutoTokenizer.from_pretrained(_MODEL_NAME)
        model = AutoModelForCausalLM.from_pretrained(_MODEL_NAME)
        model.eval()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model.to(device)
        _TORCH_STATE["bundle"] = (torch, tok, model, device)
    except Exception:
        _TORCH_STATE["bundle"] = None
    return _TORCH_STATE["bundle"]


def model_available() -> bool:
    return _load_model() is not None


@lru_cache(maxsize=2048)
def _perplexity(text: str) -> Optional[float]:
    text = text.strip()
    if len(text.split()) < 3:
        return None
    bundle = _load_model()
    if bundle is None:
        return None
    torch, tok, model, device = bundle  # type: ignore[misc]
    try:
        enc = tok(text, return_tensors="pt", truncation=True, max_length=512)
        ids = enc.input_ids.to(device)
        if ids.shape[1] < 2:
            return None
        with torch.no_grad():
            out = model(ids, labels=ids)
        nll = float(out.loss.item())
        return math.exp(min(nll, 20.0)) if math.isfinite(nll) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, x))))


def _ramp(x: float, lo: float, hi: float) -> float:
    """0 at `lo`, 1 at `hi` (works for decreasing ramps when lo > hi)."""
    if lo == hi:
        return 0.0
    t = (x - lo) / (hi - lo)
    return max(0.0, min(1.0, t))


def _mattr(tokens: list[str], window: int = 50) -> float:
    """Moving-average type-token ratio (length-independent lexical diversity)."""
    if not tokens:
        return 1.0
    if len(tokens) <= window:
        return len(set(tokens)) / len(tokens)
    vals = [len(set(tokens[i:i + window])) / window for i in range(0, len(tokens) - window + 1, 5)]
    return sum(vals) / len(vals)


def _first_word(s: str) -> str:
    m = re.match(r"[\"'“(]*([A-Za-z]+)", s)
    return m.group(1).lower() if m else ""


def _sentence_features(s: str, mean_len: float) -> dict:
    n = len(s.split())
    tells = find_tells(s)
    tw = sum(t["weight"] for t in tells)
    return {
        "words": n,
        "tells": tells,
        "tell_weight": tw,
        "transition": bool(_TRANSITION_START.match(s)),
        "tail": bool(_TAIL.search(s)),
        "triad": bool(_TRIAD.search(s)),
        "dash": bool(_DASH.search(s)),
        "mid_length": abs(n - mean_len) <= 4 and 14 <= n <= 30,
    }


def _sentence_score(f: dict) -> float:
    z = -2.0
    z += 1.6 * min(2.5, f["tell_weight"])
    z += 1.3 * f["transition"] + 1.1 * f["tail"] + 0.6 * f["triad"] + 0.5 * f["dash"]
    z += 0.6 * f["mid_length"]
    return _sigmoid(z)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def analyze(text: str) -> dict:
    """Full analysis: overall score, per-signal breakdown, tell spans and sentence scores."""
    text = (text or "").strip()
    toks = [w.lower() for w in words(text)]
    n_words = len(toks)
    sents = split_sentences(text)
    base = {
        "ai_score": 0.0,
        "score_type": "writing_pattern_index",
        "calibrated": False,
        "score_explanation": SCORE_EXPLANATION,
        "verdict": "Too short to judge",
        "confidence": "low",
        "perplexity": None,
        "perplexity_source": "model" if _TORCH_STATE.get("bundle") else "unavailable",
        "burstiness": 0.0,
        "smooth_frac": 0.0,
        "ai_tells": 0,
        "sentences": len(sents),
        "words": n_words,
        "ttr": round(_mattr(toks), 2) if toks else 1.0,
        "signals": [],
        "tells": [],
        "sentence_scores": [],
    }
    if n_words < 8:
        base["note"] = "too short to score reliably"
        return base

    lens = [len(s.split()) for s in sents] or [n_words]
    mean_len = sum(lens) / len(lens)
    cv = (statistics.pstdev(lens) / mean_len) if len(lens) >= 2 and mean_len else 0.0

    feats = [_sentence_features(s, mean_len) for s in sents]
    sent_scores = [_sentence_score(f) for f in feats]

    # 1. Burstiness — needs at least three sentences to mean anything.
    if len(sents) >= 3:
        c_burst = _ramp(cv, 0.62, 0.22)  # CV 0.22 -> fully AI-like, 0.62 -> human-like
    else:
        c_burst = 0.5
    # 2. Vocabulary — weighted tells per 100 words.
    tells = find_tells(text)
    tell_w = sum(t["weight"] for t in tells)
    density = tell_w * 100.0 / max(1, n_words)
    c_vocab = _sigmoid(2.4 * (density - 1.0))
    if not tells:
        c_vocab = 0.0
    # 3. Structure — formula rate per sentence.
    n_s = max(1, len(sents))
    rate = (sum(f["transition"] for f in feats) * 1.0 + sum(f["tail"] for f in feats) * 1.0
            + sum(f["triad"] for f in feats) * 0.6 + sum(f["dash"] for f in feats) * 0.5) / n_s
    c_struct = _sigmoid(7.0 * (rate - 0.28))
    if rate == 0:
        c_struct = 0.0
    # 4. Uniformity — repeated openers + share of mid-length sentences.
    firsts = [_first_word(s) for s in sents if _first_word(s)]
    if len(firsts) >= 3:
        top = max(firsts.count(w) for w in set(firsts)) / len(firsts)
        repeats = sum(1 for a, b in zip(firsts, firsts[1:], strict=False) if a == b) / (len(firsts) - 1)
    else:
        top, repeats = 0.0, 0.0
    mid_share = sum(f["mid_length"] for f in feats) / n_s
    c_uniform = max(0.0, min(1.0, 0.45 * _ramp(top, 0.25, 0.6) + 0.25 * _ramp(repeats, 0.0, 0.5) + 0.30 * _ramp(mid_share, 0.3, 0.8)))
    # Contractions are a mild human signal in casual prose.
    contractions = len(_CONTRACTION.findall(text))
    if contractions and n_words > 0:
        c_uniform *= max(0.6, 1.0 - contractions * 25.0 / n_words)

    # 5. Perplexity — only when a real language model is installed.
    ppl = _perplexity(text) if _load_model() is not None else None
    c_ppl = None
    if ppl is not None:
        c_ppl = _sigmoid(0.12 * (30.0 - ppl))

    comps = {
        "burstiness": c_burst,
        "vocabulary": c_vocab,
        "structure": c_struct,
        "uniformity": c_uniform,
    }
    if c_ppl is not None:
        comps["perplexity"] = c_ppl
    total_w = sum(_W[k] for k in comps)
    raw = sum(_W[k] * v for k, v in comps.items()) / total_w
    # Strong single signals should not be averaged away: blend with the max.
    peak = max(c_vocab, c_struct)
    score = 100.0 * (0.75 * raw + 0.25 * peak * (0.5 + 0.5 * c_burst))

    # Short texts are noisy — pull toward the middle a little.
    if n_words < 60:
        score = 0.7 * score + 0.3 * 35.0
    confidence = "high" if n_words >= 150 and len(sents) >= 5 else ("medium" if n_words >= 60 else "low")

    score = round(max(0.0, min(100.0, score)), 1)
    if score < 20:
        verdict = "Few flagged writing patterns"
    elif score < 45:
        verdict = "Some flagged writing patterns"
    elif score < 70:
        verdict = "Noticeable formulaic writing patterns"
    else:
        verdict = "Many formulaic writing patterns"

    signals = [
        {"key": "burstiness", "label": "Sentence rhythm", "value": round(cv, 2), "ai": round(c_burst * 100),
         "hint": "Mix short and long sentences." if c_burst > 0.5 else "Good variety of sentence lengths."},
        {"key": "vocabulary", "label": "AI vocabulary", "value": round(density, 2), "ai": round(c_vocab * 100),
         "hint": f"{len(tells)} stock word(s)/phrase(s) found." if tells else "No stock AI phrases."},
        {"key": "structure", "label": "Formulaic structure", "value": round(rate, 2), "ai": round(c_struct * 100),
         "hint": "Transition openers, -ing tails, triads or dashes." if c_struct > 0.4 else "Natural structure."},
        {"key": "uniformity", "label": "Uniformity", "value": round(top, 2), "ai": round(c_uniform * 100),
         "hint": "Many sentences start the same way or have similar length." if c_uniform > 0.5 else "Varied openings."},
    ]
    if c_ppl is not None:
        signals.append({"key": "perplexity", "label": "Predictability", "value": round(ppl, 1), "ai": round(c_ppl * 100),
                        "hint": "Very predictable word choices." if c_ppl > 0.5 else "Natural word choices."})

    # Sentence offsets for UI highlighting.
    sentence_scores = []
    cursor = 0
    for s, sc, f in zip(sents, sent_scores, feats, strict=True):
        idx = text.find(s[:20], cursor)
        start = idx if idx >= 0 else cursor
        end = start + len(s)
        cursor = end
        reasons = []
        if f["tells"]:
            reasons.append("AI vocabulary")
        if f["transition"]:
            reasons.append("formulaic opener")
        if f["tail"]:
            reasons.append("-ing tail")
        if f["triad"]:
            reasons.append("list of three")
        if f["dash"]:
            reasons.append("em dash")
        sentence_scores.append({"start": start, "end": end, "score": round(sc * 100), "reasons": reasons})

    base.update({
        "ai_score": score,
        "verdict": verdict,
        "confidence": confidence,
        "perplexity": round(ppl, 1) if ppl is not None else None,
        "perplexity_source": "model" if ppl is not None else "unavailable",
        "burstiness": round(cv * 100, 1),
        "smooth_frac": round(sum(1 for s in sent_scores if s >= 0.5) / n_s, 2),
        "ai_tells": len(tells),
        "signals": signals,
        "tells": tells,
        "sentence_scores": sentence_scores,
    })
    return base


def score_text(text: str) -> dict:
    """Compact score dict (backwards compatible): ai_score, perplexity, burstiness, smooth_frac, ai_tells, sentences, ttr."""
    a = analyze(text)
    keep = ("ai_score", "score_type", "calibrated", "score_explanation", "verdict", "confidence", "perplexity", "perplexity_source", "burstiness",
            "smooth_frac", "ai_tells", "sentences", "words", "ttr", "signals")
    out = {k: a[k] for k in keep if k in a}
    if "note" in a:
        out["note"] = a["note"]
    return out


def sentence_perplexity(sentence: str) -> Optional[float]:
    return _perplexity(sentence)


def smooth_sentences(text: str, threshold: float = 50.0) -> list[tuple[int, str, float]]:
    """Return the most machine-like sentences as (index, sentence, ai_score) sorted worst-first.

    `threshold` is an AI-likeness percentage (sentences scoring at or above it are returned).
    """
    sents = split_sentences(text)
    if not sents:
        return []
    lens = [len(s.split()) for s in sents]
    mean_len = sum(lens) / len(lens)
    out = []
    for i, s in enumerate(sents):
        sc = _sentence_score(_sentence_features(s, mean_len)) * 100
        if sc >= threshold:
            out.append((i, s, round(sc, 1)))
    out.sort(key=lambda t: -t[2])
    return out


def warmup() -> None:
    _load_model()


__all__ = ["analyze", "score_text", "split_sentences", "smooth_sentences", "sentence_perplexity", "warmup", "model_available"]
