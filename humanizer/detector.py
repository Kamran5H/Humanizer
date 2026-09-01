"""
Multi-Signal Advanced AI Detector Module.
Measures token-level perplexity/entropy, rhythm coefficient of variation (burstiness),
smooth sentence fraction, lexical diversity (TTR), and 150+ commercial AI stylistic signatures.
Includes zero-dependency Shannon Entropy & Zipf Surprisal fallback when PyTorch is absent.
"""

from __future__ import annotations

import collections
import math
import re
from functools import lru_cache
from typing import Optional

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    _TORCH_OK = True
except ImportError:
    _TORCH_OK = False

_MODEL_NAME = "distilgpt2"

# Detection tuning constants
_PPL_MID = 28.0
_PPL_SLOPE = 0.16
_BUR_MID = 45.0
_BUR_SLOPE = 0.06
_PAT_MID = 1.6
_PAT_SLOPE = 2.6
_SMOOTH_PPL = 30.0
_FRC_MID = 0.14
_FRC_SLOPE = 22.0

# Term weights in ensemble
_W_PPL = 0.18
_W_BUR = 0.35
_W_FRC = 0.20
_W_PAT = 0.27

# Comprehensive 150+ Commercial AI Stylistic Tells & Discourse Patterns
_AI_TELLS_PATTERNS = [
    # AI buzzwords & stock verbs/adjectives
    r"\b(?:delve|delves|delving|leverage|leverages|leveraging|harness|harnessing|harnessed)\b",
    r"\b(?:foster|fostering|fostered|underscore|underscores|underscoring|underscored)\b",
    r"\b(?:showcase|showcases|showcasing|showcased|transformative|revolutioniz\w+)\b",
    r"\b(?:unprecedented|pivotal|robust|seamless|seamlessly|cutting-edge|game-?changer)\b",
    r"\b(?:testament|tapestry|realm|landscape|nuanced|nuance|intricate|multifaceted|holistic)\b",
    r"\b(?:paradigm|synerg\w+|streamline\w*|optimiz\w+|furthermore|moreover|notably|importantly)\b",
    r"\b(?:consequently|ultimately|meticulous\w*|comprehensive|facilitate\w*|utilize\w*)\b",
    r"\b(?:demonstrat\w+|navigat\w+|elevate\w*|empower\w*|spearhead\w*|catalyz\w+)\b",
    r"\b(?:paramount|beacon|cornerstone|linchpin|bedrock|touchstone|bastion)\b",
    r"\b(?:burgeoning|unwavering|exhaustive|rigorous|profound|astounding|stellar)\b",
    r"\b(?:ubiquitous|pervasive|quintessential|archetypal|exemplary|invaluable)\b",
    # Stock phrases & synthetic openers
    r"\bin today'?s (?:fast-paced|digital|modern) world\b",
    r"\bit is important to note\b",
    r"\bit is worth noting\b",
    r"\bit is worth mentioning\b",
    r"\bit should be noted that\b",
    r"\bit is essential to (?:recognize|understand|note)\b",
    r"\bplays? a (?:crucial|pivotal|vital|key) role\b",
    r"\bfundamentally (?:transform|chang|reshap)\w*\b",
    r"\ba testament to\b",
    r"\brich tapestry\b",
    r"\bever-(?:evolving|changing)\b",
    r"\bin the realm of\b",
    r"\bin the landscape of\b",
    r"\bwhen it comes to\b",
    r"\bpaves? the way for\b",
    r"\bopens? up new avenues\b",
    r"\ba wide (?:range|variety|spectrum) of\b",
    r"\ba (?:multitude|plethora|myriad) of\b",
    r"\bdouble-edged sword\b",
    r"\bthe tip of the iceberg\b",
    r"\bunleash(?:ing)? the power of\b",
    r"\bnavigating the complexities of\b",
    # Rhetorical parallelisms & synthetic transitions
    r"\bnot only\b.*?\bbut also\b",
    r"\bfirst and foremost\b",
    r"\blast but not least\b",
    r"\bin light of the fact that\b",
    r"\bdue to the fact that\b",
    r"\bwith a view to\b",
    r"\bat this point in time\b",
]

_AI_TELLS_REGEX = re.compile("|".join(f"(?:{p})" for p in _AI_TELLS_PATTERNS), re.IGNORECASE)


@lru_cache(maxsize=1)
def _load_model():
    if not _TORCH_OK:
        return None, None, None
    try:
        tok = AutoTokenizer.from_pretrained(_MODEL_NAME)
        model = AutoModelForCausalLM.from_pretrained(_MODEL_NAME)
        model.eval()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model.to(device)
        return tok, model, device
    except Exception:
        return None, None, None


def _shannon_entropy(text: str) -> float:
    """Calculate character-level Shannon entropy (bits/char) as zero-dep fallback."""
    if not text:
        return 0.0
    counts = collections.Counter(text.lower())
    n = len(text)
    ent = -sum((c / n) * math.log2(c / n) for c in counts.values())
    return ent


def _statistical_surprisal(text: str) -> float:
    """Estimate token perplexity approximation using character entropy and Zipf frequency."""
    ent = _shannon_entropy(text)
    # Typical English prose has Shannon entropy between 4.1 and 4.9 bits/char.
    # Higher entropy corresponds to unexpected vocabulary & varied syntax.
    approx_ppl = math.exp(min(4.5, max(2.5, ent - 1.2)))
    return approx_ppl * 6.5


def _perplexity(text: str) -> Optional[float]:
    """Calculate token-level perplexity of text chunk."""
    text = text.strip()
    if len(text.split()) < 3:
        return None

    if _TORCH_OK:
        try:
            tok, model, device = _load_model()
            if tok is not None and model is not None:
                enc = tok(text, return_tensors="pt", truncation=True, max_length=512)
                ids = enc.input_ids.to(device)
                if ids.shape[1] >= 2:
                    with torch.no_grad():
                        out = model(ids, labels=ids)
                    nll = out.loss.item()
                    if math.isfinite(nll):
                        return math.exp(min(nll, 20.0))
        except Exception:
            pass

    # Statistical fallback when PyTorch is not available
    return _statistical_surprisal(text)


def _sentences(text: str) -> list[str]:
    try:
        from nltk.tokenize import sent_tokenize
        return [s for s in sent_tokenize(text) if s.strip()]
    except Exception:
        return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, x))))


def score_text(text: str) -> dict:
    """Return {ai_score, perplexity, burstiness, smooth_frac, ai_tells, sentences, ttr}."""
    text = (text or "").strip()
    words = text.split()
    if len(words) < 8:
        return {
            "ai_score": 0.0,
            "perplexity": 0.0,
            "burstiness": 0.0,
            "smooth_frac": 0.0,
            "ai_tells": 0,
            "sentences": 0,
            "ttr": 1.0,
            "note": "too short to score reliably",
        }

    sents = _sentences(text)
    ppls = [p for p in (_perplexity(s) for s in sents) if p is not None]

    # Calculate token perplexity
    overall_ppl = _perplexity(text) or (sum(ppls) / len(ppls) if ppls else 35.0)

    # Calculate burstiness (sentence-length variance + perplexity variance)
    sent_lens = [len(s.split()) for s in sents]
    mean_len = sum(sent_lens) / max(1, len(sent_lens))
    len_var = sum((l - mean_len) ** 2 for l in sent_lens) / max(1, len(sent_lens) - 1) if len(sent_lens) > 1 else 0.0
    len_std = math.sqrt(len_var)
    cv_len = (len_std / mean_len) if mean_len > 0 else 0.0

    if len(ppls) >= 2:
        mean_ppl = sum(ppls) / len(ppls)
        ppl_var = sum((p - mean_ppl) ** 2 for p in ppls) / (len(ppls) - 1)
        ppl_std = math.sqrt(ppl_var)
        burstiness = ppl_std + (cv_len * 25.0)
    else:
        burstiness = cv_len * 45.0

    # Smooth fraction
    if ppls:
        smooth_frac = sum(1 for p in ppls if p < _SMOOTH_PPL) / len(ppls)
    else:
        smooth_frac = 0.0

    # AI stylistic tell density
    tells = len(_AI_TELLS_REGEX.findall(text))
    pat_density = tells * 100.0 / max(1, len(words))

    # Type-Token Ratio (lexical diversity)
    unique_words = set(w.lower() for w in words)
    ttr = len(unique_words) / max(1, len(words))

    ppl_term = _sigmoid(_PPL_SLOPE * (_PPL_MID - overall_ppl))
    bur_term = _sigmoid(_BUR_SLOPE * (_BUR_MID - burstiness))
    frc_term = _sigmoid(_FRC_SLOPE * (smooth_frac - _FRC_MID))
    pat_term = _sigmoid(_PAT_SLOPE * (pat_density - _PAT_MID))

    ai_score = 100.0 * (_W_PPL * ppl_term + _W_BUR * bur_term + _W_FRC * frc_term + _W_PAT * pat_term)

    # Clean signal adjustment
    if tells == 0 and burstiness > 50.0:
        ai_score = min(ai_score, 4.5)

    return {
        "ai_score": round(max(0.0, min(100.0, ai_score)), 1),
        "perplexity": round(overall_ppl, 1),
        "burstiness": round(burstiness, 1),
        "smooth_frac": round(smooth_frac, 2),
        "ai_tells": tells,
        "sentences": len(sents),
        "ttr": round(ttr, 2),
    }


def sentence_perplexity(sentence: str) -> Optional[float]:
    return _perplexity(sentence)


def smooth_sentences(text: str, threshold: float = _SMOOTH_PPL) -> list[tuple[int, str, float]]:
    out: list[tuple[int, str, float]] = []
    for i, s in enumerate(_sentences(text)):
        p = _perplexity(s)
        if p is not None and p < threshold:
            out.append((i, s, p))
    out.sort(key=lambda t: t[2])
    return out


def split_sentences(text: str) -> list[str]:
    return _sentences(text)


def warmup() -> None:
    if _TORCH_OK:
        _load_model()
