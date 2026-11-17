"""
Core humanizer engine.

Two back-ends behind one API:

* **LLM** — persona-driven rewrite through the provider pool, best-of-N
  selection with the local detector, guided re-rolls for the sentences that
  still read as machine-written, and strict fidelity checks (numbers,
  citations, URLs and formatting placeholders must survive).
* **Offline rules** — :func:`humanizer.rules.rewrite`; used automatically when
  no API key is configured or every LLM attempt fails.

Text structure (Markdown headings, bullets, code fences, blank lines) is kept;
only prose is rewritten.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Optional

import docx

from humanizer.config import HumanizerConfig
from humanizer.detector import score_text, smooth_sentences
from humanizer.document import (  # noqa: F401  (re-exported for backwards compatibility)
    _TOKEN_PATTERN,
    Checkpoint,
    RunStyle,
    _extract_run_style,
    _is_heading,
    _reconstruct_runs,
    _tag_runs,
    iter_document_paragraphs,
    save_docx_package,
    select_paragraphs,
    should_skip_paragraph,
)
from humanizer.nlp import fix_encoding as _fix_encoding
from humanizer.nlp import is_faithful as _is_faithful
from humanizer.patterns import BANNED_FOR_PROMPT, BANNED_PHRASES_FOR_PROMPT
from humanizer.providers import call_llm_pool, has_providers
from humanizer.rules import lock_elements, polish, rewrite
from humanizer.text import ing_to_3sg, split_sentences

logger = logging.getLogger(__name__)

MIN_WORDS_TO_REWRITE = 6

_PERSONAS = [
    "a sharp newspaper columnist who writes tight, concrete sentences and hates filler",
    "a working professional explaining something clearly to a smart colleague",
    "a magazine feature writer with a calm, vivid voice",
    "a knowledgeable blogger who writes in a relaxed but precise voice",
    "a careful technical writer who is plain-spoken and never pads",
    "an experienced essayist who mixes short, punchy lines with longer, winding ones",
]
_SCI_PERSONAS = [
    "a senior scientist writing a concise, evidence-driven paper without padding",
    "a journal editor who turns dense drafts into crisp, direct prose",
    "an experienced academic author with a natural sentence rhythm and active verbs",
    "a professor explaining research findings clearly and precisely",
    "a domain specialist writing an objective manuscript with exact terminology",
]

_BANNED_WORDS = ", ".join(BANNED_FOR_PROMPT)
_BANNED_PHRASES = "; ".join(f'"{p}"' for p in BANNED_PHRASES_FOR_PROMPT)

# ---------------------------------------------------------------------------
# Output cleaning
# ---------------------------------------------------------------------------
_WRAPPER_LEAD = re.compile(
    r"^\s*(?:```[a-z]*\s*)?(?:(?:sure|certainly|of course|okay|ok)[,!.]?\s*)?"
    r"(?:here(?:'s| is| are)[^:\n]{0,80}:\s*|"
    r"(?:the\s+)?(?:rewritten|revised|humaniz(?:ed|er)|edited|new|final)\s+(?:text|paragraph|version|line|sentence)[^:\n]{0,40}:\s*)",
    re.IGNORECASE,
)
_COT_TELLS = (
    "<think", "</think", "thinking process", "analyze user input", "mental iteration",
    "constraint check", "check against constraints", "here's a thinking process",
    "rewrite idea", "refined draft",
)
_META_LINE = re.compile(
    r"^(?:[-*•]|\d+[.)])?\s*\**(?:note|notes|explanation|changes made|word count|original|"
    r"rewritten|draft \d|constraint|register|vocabulary|banned words|target text|context)\b[^:\n]{0,30}:",
    re.IGNORECASE,
)


def _is_thinking_trace(text: str) -> bool:
    low = (text or "").lower()
    return any(t in low for t in _COT_TELLS)


def _strip_wrappers(text: str) -> str:
    """Remove reasoning blocks, preambles, code fences, quotes and trailing notes from model output."""
    if not text:
        return ""
    text = re.sub(r"<(think|thought|reasoning)>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(?:think|thought|reasoning)>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</(?:think|thought|reasoning)>", "", text, flags=re.IGNORECASE)
    m = re.search(r"\*\*(?:answer|response|output|rewritten text|final)\*\*\s*:?\s*", text, flags=re.IGNORECASE)
    if m:
        text = text[m.end():]
    prev = None
    while prev != text:
        prev = text
        text = _WRAPPER_LEAD.sub("", text).strip()
    text = re.sub(r"^\s*```[a-z]*\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    lines = []
    for ln in text.splitlines():
        if _META_LINE.match(ln.strip()):
            break  # everything after "Note:" etc. is commentary
        lines.append(ln)
    text = "\n".join(lines).strip()
    if len(text) >= 2 and text[0] in "\"“'" and text[-1] in "\"”'" and text.count('"') <= 2:
        text = text[1:-1].strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "" if _is_thinking_trace(text) else text.strip()


def _guard_fidelity(src: str, out: str) -> str:
    """Normalize whitespace only; invented facts must be rejected, not hidden."""
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def _break_ai_syntactic_patterns(text: str) -> str:
    """Split trailing participial chains (', thereby enabling X' -> '. This enables X')."""
    if not text:
        return ""
    text = re.sub(r"(?:,\s*|\s+and\s+)(?:thereby|thus)\s+(\w+ing)\b",
                  lambda m: f". This {ing_to_3sg(m.group(1))}", text, flags=re.IGNORECASE)
    text = re.sub(r",\s*leading to\b", ". This leads to", text, flags=re.IGNORECASE)
    text = re.sub(r",\s*resulting in\b", ". This results in", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _inflect_ing_to_3sg(verb_ing: str) -> str:  # backwards compatibility
    return ing_to_3sg(verb_ing)


def _clean_rewrite(src: str, out: str, cfg: Optional[HumanizerConfig] = None) -> str:
    if out.strip() == src.strip():
        return src
    out = _strip_wrappers(out)
    if not out:
        return ""
    out = _fix_encoding(out)
    out = _guard_fidelity(src, out)
    # Clean leftover stock phrases from the model output, but only keep the
    # polished version if it is still faithful to the source.
    polished = polish(out, cfg)
    if polished != out and check_fidelity(src, polished, cfg, short=True)[0]:
        return polished
    return out


# ---------------------------------------------------------------------------
# Fidelity checks
# ---------------------------------------------------------------------------
_NUM = re.compile(r"(?<![\w.])[+−-]?\d+(?:[.,]\d+)*(?:[eE][+−-]?\d+)?(?![\w])")
_PLACEHOLDER = re.compile(r"__(?:RUN_LOCKED|RUN_PLACEHOLDER|LOCK_[A-Z_]+)_\d+__")
_TAG_OPEN = re.compile(r"<(bi|b|i|u)\s+id=['\"](\d+)['\"]>", re.IGNORECASE)
_CITE = re.compile(r"\[\d+(?:\s*[,\-–]\s*\d+)*\]|\([A-Z][A-Za-z'’\-]+(?: et al\.?)?,? (?:19|20)\d{2}[a-z]?\)")
# Meaning guards. These are lexicon based, so they are tuned to catch the
# errors that matter (a dropped negation, a removed hedge, an added certainty,
# a reversed trend) while letting ordinary rewording through: "did not reduce"
# -> "failed to lower" and "rose" -> "climbed" must pass; "did not reduce" ->
# "reduced" and "rose" -> "fell" must fail.
_NEGATION = re.compile(
    r"\b(?:no|never|neither|nor|none|nobody|nothing|nowhere|without|cannot|unable|impossible|"
    r"fail(?:s|ed|ing)?\s+to|lack(?:s|ed|ing)?|absence\s+of|absent|neglig\w*|insignificant)\b|"
    r"\bnot\b(?!\s+only\b)|n['’]t\b",
    re.IGNORECASE,
)
_UNCERTAINTY = re.compile(
    r"\b(?:may|might|could|can|potential(?:ly)?|possib(?:le|ly|ility)|likely|unlikely|probabl[ey]|perhaps|"
    r"suggest(?:s|ed|ing|ive)?|indicat(?:e|es|ed|ing|ive)|appear(?:s|ed)?|seem(?:s|ed)?|tend(?:s|ed)?\s+to|"
    r"point(?:s|ed)?\s+to|hint(?:s|ed)?|preliminary|tentative(?:ly)?|plausibl[ey]|presumabl[ey]|"
    r"hypothes\w+|estimat\w+|approximately|about|around|roughly|some)\b",
    re.IGNORECASE,
)
_WEAK_HEDGE = re.compile(r"\b(?:may|might|could|possibly|perhaps|potential(?:ly)?|conceivabl[ey])\b", re.IGNORECASE)
_STRONG_HEDGE = re.compile(r"\b(?:likely|probabl[ey]|presumabl[ey]|most\s+likely|almost\s+certainly)\b", re.IGNORECASE)
_CERTAINTY = re.compile(
    r"\b(?:prov(?:e|es|ed|en|ing)|definitive(?:ly)?|definitely|certain(?:ly)?|undoubtedly|unquestionabl[ey]|"
    r"conclusive(?:ly)?|always|guarantee[sd]?|establish(?:es|ed)\s+that|clearly\s+(?:show|demonstrat)\w*)\b",
    re.IGNORECASE,
)
_DIRECTION = re.compile(
    r"\b(?P<up>increas\w*|rais(?:e|es|ed|ing)|rose|ris(?:e|es|en|ing)|gr[eo]w(?:s|n|ing|th)?|climb\w*|boost\w*|"
    r"surg\w*|jump\w*|soar\w*|doubl\w*|tripl\w*|expand\w*|higher|greater|more|gain(?:s|ed)?)\b|"
    r"\b(?P<down>decreas\w*|reduc\w*|lower(?:s|ed|ing)?|fell|fall(?:s|en|ing)?|drop\w*|declin\w*|cut(?:s|ting)?|"
    r"shr[iau]nk\w*|diminish\w*|plung\w*|halv\w*|less|fewer|loss(?:es)?|lost)\b",
    re.IGNORECASE,
)


def _directions(text: str) -> Counter:
    return Counter(m.lastgroup for m in _DIRECTION.finditer(text))


def _norm_fact(span: str) -> str:
    span = span.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'").replace("−", "-")
    return re.sub(r"\s+", "", span)


def _facts(text: str) -> set[str]:
    _, vault = lock_elements(text)
    return {_norm_fact(span) for token, span in vault.items()
            if not token.startswith(("__LOCK_TAG_", "__LOCK_PLACEHOLDER_"))}


def check_fidelity(src: str, cand: str, cfg: Optional[HumanizerConfig] = None, *, short: bool = False) -> tuple[bool, str]:
    """Return (ok, reason).

    Hard requirements: every number, citation, URL, quantity, statistic, quotation,
    placeholder and formatting tag in `src` survives and nothing new is invented;
    no negation is dropped; uncertainty is neither removed nor upgraded to
    certainty; no quantitative trend is reversed; the meaning stays on topic.
    """
    if not cand or not cand.strip():
        return False, "empty"
    if _is_thinking_trace(cand):
        return False, "reasoning trace"

    # 1. Structure the document rebuild depends on.
    if Counter(_PLACEHOLDER.findall(src)) != Counter(_PLACEHOLDER.findall(cand)):
        return False, "placeholder dropped or duplicated"
    src_tags = Counter(m.groups() for m in _TAG_OPEN.finditer(src))
    cand_tags = Counter(m.groups() for m in _TAG_OPEN.finditer(cand))
    complete = Counter((m.group("tag"), m.group("id")) for m in _TOKEN_PATTERN.finditer(cand) if m.group("tag"))
    closing = Counter(t.lower() for t in re.findall(r"</(bi|b|i|u)(?:\s+id=['\"]\d+['\"])?\s*>", cand, re.IGNORECASE))
    if src_tags != cand_tags or cand_tags != complete or Counter(t[0].lower() for t in cand_tags.elements()) != closing:
        return False, "formatting tags changed or malformed"

    # 2. Hard facts: nothing dropped, nothing invented.
    # Every occurrence must survive (a repeated value can carry a separate claim);
    # nothing new may appear. Repeating an existing value is harmless.
    src_cites, cand_cites = Counter(_CITE.findall(src)), Counter(_CITE.findall(cand))
    if src_cites - cand_cites or set(cand_cites) - set(src_cites):
        return False, "citation dropped or introduced"
    src_nums = Counter(n.replace(",", "").replace("−", "-") for n in _NUM.findall(src))
    cand_nums = Counter(n.replace(",", "").replace("−", "-") for n in _NUM.findall(cand))
    if src_nums - cand_nums or set(cand_nums) - set(src_nums):
        return False, "number changed"
    if _facts(src) - _facts(cand):
        return False, "protected fact changed"

    # 3. Meaning guards.
    src_neg, cand_neg = len(_NEGATION.findall(src)), len(_NEGATION.findall(cand))
    if cand_neg < src_neg or cand_neg > src_neg + 1:
        return False, "negation changed"
    src_hedge, cand_hedge = len(_UNCERTAINTY.findall(src)), len(_UNCERTAINTY.findall(cand))
    if src_hedge and not cand_hedge:
        return False, "uncertainty changed"
    if len(_CERTAINTY.findall(cand)) > len(_CERTAINTY.findall(src)):
        return False, "uncertainty changed"
    # 'might' -> 'likely' upgrades a possibility to a probability.
    src_weak, cand_weak = len(_WEAK_HEDGE.findall(src)), len(_WEAK_HEDGE.findall(cand))
    if src_weak and not cand_weak and len(_STRONG_HEDGE.findall(cand)) > len(_STRONG_HEDGE.findall(src)):
        return False, "uncertainty changed"
    sd, cd = _directions(src), _directions(cand)
    for way, other in (("up", "down"), ("down", "up")):
        if sd[way] and not cd[way] and cd[other] > sd[other]:
            return False, "direction of claim changed"
    if not _is_faithful(src, cand):
        return False, "meaning drift"

    if not short:
        sw, cw = len(src.split()), len(cand.split())
        lo = cfg.min_length_ratio if cfg else 0.75
        hi = cfg.max_length_ratio if cfg else 1.45
        if cw < sw * lo or cw > max(sw * hi, sw + 12):
            return False, "length out of range"
    return True, ""


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------
def _avoid_block(avoid_smooth: Optional[list[str]]) -> str:
    picks = [s.strip() for s in (avoid_smooth or []) if s.strip()][:3]
    if not picks:
        return ""
    bullets = "\n".join(f'  - "{s[:180]}"' for s in picks)
    return ("\n\nThe previous attempt still sounded machine-written in these sentences. Recast them hardest "
            "(different clause order, more specific words, different length) while keeping every fact:\n" + bullets)


_KEY_QUALIFIERS = re.compile(
    r"\b(?:not|no|never|without|cannot|may|might|could|possibly|potentially|likely|unlikely|probably|perhaps|"
    r"suggest(?:s|ed)?|appear(?:s|ed)?|seem(?:s|ed)?)\b|n['’]t\b",
    re.IGNORECASE,
)


def build_stealth_prompt(text: str, persona: str, is_short: bool, avoid_smooth: Optional[list[str]] = None,
                         scientific: bool = False) -> str:
    keep = ("ACCURACY (non-negotiable): keep every fact, claim, number, unit, name, date, quotation, technical term, "
            "citation such as [3] or (Smith, 2020), and URL exactly as in the source. Keep every negation and every "
            "hedge: never turn 'may', 'suggests' or 'could' into a certainty, and never drop a 'not'. Keep the direction "
            "of every trend (an increase stays an increase). Add no facts, examples, sources or URLs; remove no information. "
            "Keep every placeholder such as __RUN_LOCKED_0__ and every tag such as <b id='2'>…</b> exactly once, around the same words.")
    quals = []
    for q in _KEY_QUALIFIERS.findall(text):
        q = q.lower()
        if q not in quals:
            quals.append(q)
    if quals:
        keep += " Qualifiers in this text that must keep their meaning: " + ", ".join(quals[:12]) + "."
    if is_short:
        return (f"Rewrite this short line as {persona} would, plainly and without hype. {keep} "
                f"Keep it about the same length. Avoid: {_BANNED_WORDS}.\n"
                "Return only the rewritten line.\n\nLINE:\n" + text)
    register = ("Formal academic register: no contractions, no slang, no rhetorical questions. Prefer active verbs "
                "('we measured', 'the catalyst lowers') over heavy nominalisations. Keep domain terminology exactly."
                if scientific else
                "Natural professional register: contractions are fine, active voice, no slang, no hype.")
    n = len(text.split())
    return f"""Rewrite the TEXT so it reads as if {persona} wrote it by hand.

{keep}

STYLE:
- Vary sentence length a lot: mix short sentences (4-9 words) with longer ones (20-30 words). Never write three sentences of similar length in a row.
- Vary how sentences begin. Do not open sentences with Furthermore, Moreover, Additionally, Notably, Ultimately or In conclusion.
- Use plain, specific words. Say "is", "are" or "has" instead of "serves as", "stands as" or "boasts".
- No em dashes. No "not only ... but also". No ", highlighting/underscoring/fostering ..." tails.
- Never use these words: {_BANNED_WORDS}.
- Never use these phrases: {_BANNED_PHRASES}.
- {register}
- Rewrite, do not summarise: every sentence's information must still be there, with the same line breaks.
- The source has {n} words; return between {round(n * 0.85)} and {round(n * 1.15)} words.{_avoid_block(avoid_smooth)}

Return only the rewritten text: no preamble, notes, quotes or markdown fences.

TEXT:
{text}"""


# ---------------------------------------------------------------------------
# Paragraph rewriting
# ---------------------------------------------------------------------------
def resolve_engine(cfg: HumanizerConfig) -> str:
    """'llm' when the LLM pool is requested and configured, else 'rules'."""
    if cfg.use_gemini:
        if has_providers():
            return "llm"
        if not cfg.allow_offline_fallback:
            raise ValueError("No AI provider configured. Add an API key in .env and restart, or select Offline explicitly. "
                             "ChatGPT/Claude app subscriptions do not configure API access.")
    return "rules"


def humanize_paragraph_rules(text: str, cfg: HumanizerConfig) -> str:
    """Offline rule pipeline for one paragraph."""
    text = _fix_encoding(text)
    if not text.strip():
        return text
    cfg.count("rules")
    candidate = rewrite(text, cfg)
    ok, reason = check_fidelity(text, candidate, cfg, short=True)
    if not ok:
        cfg.count("kept")
        cfg.status(f"Original paragraph retained: offline rewrite failed the {reason} check.")
        return text
    return candidate


def _roughen_smooth_sentences(text: str, cfg: HumanizerConfig) -> str:
    """Re-roll up to three sentences that still read as machine-written."""
    flagged = smooth_sentences(text, threshold=55.0)[:3]
    if not flagged:
        return text
    sents = split_sentences(text)
    personas = _SCI_PERSONAS if cfg.scientific else _PERSONAS
    changed = False
    for idx, sent, _ in flagged:
        if cfg.cancelled or idx >= len(sents):
            break
        prev_ctx = sents[idx - 1] if idx > 0 else ""
        next_ctx = sents[idx + 1] if idx + 1 < len(sents) else ""
        prompt = (
            f"Rewrite this one sentence so it sounds like {cfg.rng.choice(personas)} wrote it. Same meaning, "
            f"{'formal academic register' if cfg.scientific else 'plain professional register'}.\n"
            f"Keep every number, citation, term and placeholder exactly. Avoid: {_BANNED_WORDS}.\n"
            + (f"Previous sentence: {prev_ctx[:200]}\n" if prev_ctx else "")
            + (f"Next sentence: {next_ctx[:200]}\n" if next_ctx else "")
            + f"Return only the rewritten sentence.\n\nSENTENCE:\n{sent}"
        )
        try:
            cand = _clean_rewrite(sent, call_llm_pool(prompt, cfg, temperature=1.0), cfg)
        except Exception as e:
            logger.debug("Sentence refinement failed: %s", e)
            continue
        cand_sents = split_sentences(cand)
        if not cand_sents:
            continue
        cand = " ".join(cand_sents)
        ok, _ = check_fidelity(sent, cand, cfg, short=True)
        sw, cw = len(sent.split()), len(cand.split())
        if ok and cand != sent and max(3, sw * 0.5) <= cw <= max(35, sw * 1.8):
            sents[idx] = cand
            changed = True
    return " ".join(sents) if changed else text


def humanize_paragraph_stealth(text: str, cfg: HumanizerConfig) -> str:
    """LLM rewrite with best-of-N selection, fidelity gates and an offline fallback."""
    text = _fix_encoding(text)
    if not text.strip():
        return text
    n_words = len(text.split())
    is_short = n_words < 15
    budget = 1 if cfg.stealth_level <= 1 or is_short else cfg.retry_budget
    personas = _SCI_PERSONAS if cfg.scientific else _PERSONAS
    start = cfg.rng.randrange(len(personas))

    best_text: Optional[str] = None
    best_score = 101.0
    last_failure = "No candidate received."
    hints: list[str] = []
    for attempt in range(budget):
        if cfg.cancelled:
            break
        persona = personas[(start + attempt) % len(personas)]
        temp = min(1.2, cfg.gemini_temperature + 0.08 * attempt)
        prompt = build_stealth_prompt(text, persona, is_short, avoid_smooth=hints, scientific=cfg.scientific)
        try:
            raw = call_llm_pool(prompt, cfg, temp)
        except Exception as e:
            last_failure = f"Provider request failed ({type(e).__name__}); check provider status."
            logger.warning("LLM rewrite failed: %s", e)
            if "No LLM providers" in str(e) or "cooling down" in str(e) or "All LLM providers failed" in str(e):
                break
            continue
        cand = _clean_rewrite(text, raw, cfg)
        ok, reason = check_fidelity(text, cand, cfg, short=is_short)
        if not ok:
            last_failure = f"Candidate rejected: {reason}."
            cfg.status(last_failure)
            logger.debug("Candidate rejected: %s", reason)
            continue
        if budget == 1:
            best_text = cand
            break
        score = score_text(cand).get("ai_score", 100.0)
        if score < best_score:
            best_score, best_text = score, cand
        if best_score <= cfg.target_pct:
            break
        hints = [s for _, s, _ in smooth_sentences(best_text, threshold=55.0)[:3]]

    if best_text is None:
        if not cfg.allow_offline_fallback and not cfg.cancelled:
            raise ValueError(f"No safe AI rewrite was produced. {last_failure} Check provider status and retry. "
                             "The request failed or candidates did not preserve the source; no offline substitution was made.")
        cfg.status("LLM unavailable for this paragraph — using offline rules.")
        return humanize_paragraph_rules(text, cfg)
    cfg.count("llm")
    if not is_short and cfg.stealth_level >= 3 and not cfg.cancelled:
        refined = _roughen_smooth_sentences(best_text, cfg)
        ok, reason = check_fidelity(text, refined, cfg)
        if ok:
            best_text = refined
        else:
            cfg.status(f"Sentence refinement discarded: {reason}.")
    return best_text


def _rewrite_unit(text: str, cfg: HumanizerConfig, engine: str, min_words: int = MIN_WORDS_TO_REWRITE) -> str:
    if len(re.findall(r"[A-Za-z]{2,}", text)) < min_words:
        return text
    if engine == "llm":
        return humanize_paragraph_stealth(text, cfg)
    return humanize_paragraph_rules(text, cfg)


# ---------------------------------------------------------------------------
# Plain text / Markdown
# ---------------------------------------------------------------------------
_LINE_PREFIX = re.compile(r"^(\s*(?:#{1,6}\s+|[-*+•]\s+|\d+[.)]\s+|>\s*|\[[ xX]\]\s+)*)")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _segment(text: str) -> list[tuple[str, str, str, bool]]:
    """Split text into (prefix, body, suffix, rewrite?) units that preserve layout.

    Hard-wrapped prose (e.g. pasted from a PDF) is re-joined into one paragraph;
    Markdown headings, bullets, quotes and code fences keep their markers.
    """
    units: list[tuple[str, str, str, bool]] = []
    block: list[tuple[str, str]] = []  # (line, terminator)

    def flush() -> None:
        if not block:
            return
        lines = [ln for ln, _ in block]
        structured = any(_LINE_PREFIX.match(ln).group(1).strip() for ln in lines)
        avg = sum(len(ln) for ln in lines) / len(lines)
        unterminated = sum(1 for ln in lines[:-1] if not ln.rstrip().endswith((".", "!", "?", ":", ";")))
        hard_wrapped = len(lines) > 1 and not structured and avg > 45 and unterminated >= (len(lines) - 1) / 2
        if hard_wrapped:
            lead = lines[0][: len(lines[0]) - len(lines[0].lstrip())]
            units.append((lead, " ".join(ln.strip() for ln in lines), block[-1][1], True))
        else:
            for ln, term in block:
                prefix = _LINE_PREFIX.match(ln).group(1)
                body = ln[len(prefix):]
                trail = body[len(body.rstrip()):]
                units.append((prefix, body.rstrip(), trail + term, True))
        block.clear()

    in_fence = False
    raw_lines = text.split("\n")
    for i, line in enumerate(raw_lines):
        term = "\n" if i < len(raw_lines) - 1 else ""
        if _FENCE.match(line):
            flush()
            in_fence = not in_fence
            units.append(("", line + term, "", False))
        elif in_fence or not line.strip():
            flush()
            units.append(("", line + term, "", False))
        else:
            block.append((line, term))
    flush()
    return units


def _join(units: list[tuple[str, str, str, bool]]) -> str:
    return "".join(p + b + s for p, b, s, _ in units)


def humanize_text(text: str, cfg: HumanizerConfig) -> str:
    """Humanize multi-paragraph text (Markdown-aware) with progress reporting and resume support."""
    if not text or not text.strip():
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    units = _segment(text)
    todo = [i for i, u in enumerate(units) if u[3] and len(re.findall(r"[A-Za-z]{2,}", u[1])) >= MIN_WORDS_TO_REWRITE]
    total = len(todo)
    engine = resolve_engine(cfg)
    cfg.status("Using LLM provider pool" if engine == "llm" else "Using offline rule engine (no API key configured)")
    if total == 0:
        cfg.report_progress(1, 1, "Nothing to rewrite")
        return text

    cfg.report_progress(0, total, "Starting…")
    ckpt = Checkpoint(Checkpoint.compute_id(text, {**cfg.to_dict(), "engine": engine}), text, cfg.to_dict(), total=total, mode="text")
    results: dict[int, str] = {}

    def work(i: int) -> tuple[int, str]:
        cached = ckpt.get(i)
        if cached is not None and check_fidelity(units[i][1], cached, cfg)[0]:
            return i, cached
        if cfg.cancelled:
            return i, units[i][1]
        try:
            res = _rewrite_unit(units[i][1], cfg, engine)
        except Exception as e:  # never lose a paragraph to an unexpected error
            if engine == "llm" and not cfg.allow_offline_fallback:
                raise
            logger.exception("Paragraph %d failed: %s", i, e)
            res = units[i][1]
        if not cfg.cancelled:
            ckpt.put(i, res)
        return i, res

    done = 0
    workers = 1 if engine == "rules" else max(1, min(cfg.parallel_workers, total))
    if workers == 1:
        for i in todo:
            if cfg.cancelled:
                break
            _, results[i] = work(i)
            done += 1
            cfg.report_progress(done, total, f"Paragraph {done}/{total}")
    else:
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(work, i) for i in todo]
            for fut in cf.as_completed(futs):
                i, res = fut.result()
                results[i] = res
                done += 1
                cfg.report_progress(done, total, f"Paragraph {done}/{total}")
                if cfg.cancelled:
                    for f in futs:
                        f.cancel()
                    break

    new_units = [(p, results.get(i, b), s, r) for i, (p, b, s, r) in enumerate(units)]
    if not cfg.cancelled:
        ckpt.clear()
        cfg.report_progress(total, total, "Done")
    return _join(new_units)


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def humanize_docx(src: Path, cfg: HumanizerConfig, dst: Optional[Path] = None) -> Path:
    """Humanize a Word document in place (formatting, fields, links and citations preserved)."""
    src = Path(src)
    doc = docx.Document(str(src))
    target = Path(dst) if dst else src.with_name(f"{src.stem}_humanized{src.suffix}")
    paras = select_paragraphs(doc, rewrite_headings=cfg.rewrite_headings)
    tagged: list[tuple[str, dict]] = [_tag_runs(p) for p in paras]
    minimums = [2 if cfg.rewrite_headings and _is_heading(p) else MIN_WORDS_TO_REWRITE for p in paras]
    todo = [i for i, (t, _) in enumerate(tagged) if len(re.findall(r"[A-Za-z]{2,}", t)) >= minimums[i]]
    total = len(todo)
    engine = resolve_engine(cfg)
    cfg.status("Using LLM provider pool" if engine == "llm" else "Using offline rule engine (no API key configured)")
    if total == 0:
        save_docx_package(src, doc, target)
        cfg.report_progress(1, 1, "Nothing to rewrite")
        return target

    key_text = "\n\n".join(t for t, _ in tagged)
    ckpt = Checkpoint(Checkpoint.compute_id(key_text, {**cfg.to_dict(), "engine": engine}), key_text, cfg.to_dict(),
                      total=total, mode="docx", source_path=str(src))
    cfg.report_progress(0, total, "Starting…")
    results: dict[int, str] = {}

    def work(i: int) -> tuple[int, str]:
        cached = ckpt.get(i)
        if cached is not None and check_fidelity(tagged[i][0], cached, cfg)[0]:
            return i, cached
        t = tagged[i][0]
        if cfg.cancelled:
            return i, t
        try:
            res = _rewrite_unit(t, cfg, engine, min_words=minimums[i])
        except Exception as e:
            if engine == "llm" and not cfg.allow_offline_fallback:
                raise
            logger.exception("DOCX paragraph %d failed: %s", i, e)
            res = t
        if not cfg.cancelled:
            ckpt.put(i, res)
        return i, res

    done = 0
    workers = 1 if engine == "rules" else max(1, min(cfg.parallel_workers, total))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, i) for i in todo]
        for fut in cf.as_completed(futs):
            i, res = fut.result()
            results[i] = res
            done += 1
            cfg.report_progress(done, total, f"Paragraph {done}/{total}")
            if cfg.cancelled:
                for f in futs:
                    f.cancel()
                break

    cfg.report_progress(done, total, "Rebuilding document formatting…")
    for i, res in results.items():
        if res != tagged[i][0]:
            _reconstruct_runs(paras[i], res, tagged[i][1])
    if not cfg.cancelled:
        ckpt.clear()
        cfg.report_progress(total, total, "Done")
    save_docx_package(src, doc, target)
    return target


__all__ = [
    "humanize_text", "humanize_docx", "humanize_paragraph_stealth", "humanize_paragraph_rules",
    "build_stealth_prompt", "check_fidelity", "resolve_engine",
]
