"""
Core Stealth Humanizer Engine Module.
Handles persona prompts, guided reflexion re-rolls, surgical roughening,
fidelity guards, and parallel document humanization for text and DOCX files.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import os
import random
import re
import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from docx import Document

from humanizer.config import HumanizerConfig
from humanizer.detector import score_text, smooth_sentences, sentence_perplexity, split_sentences
from humanizer.document import (
    Checkpoint,
    RunStyle,
    _extract_run_style,
    _tag_runs,
    _reconstruct_runs,
    should_skip_paragraph,
    iter_document_paragraphs,
)
from humanizer.providers import call_llm_pool
from humanizer.rules import (
    lock_elements,
    unlock_elements,
    apply_phrase_subs,
    apply_word_subs,
    apply_contractions,
    fix_grammar,
)
try:
    from nlp_enhance import fix_encoding as _fix_encoding, is_faithful as _is_faithful
except Exception:
    def _fix_encoding(t: str) -> str:  # type: ignore[misc]
        return t
    def _is_faithful(src: str, out: str, **kwargs) -> bool:  # type: ignore[misc]
        return True

logger = logging.getLogger(__name__)

# Personas
_PERSONAS = [
    "a sharp newspaper columnist who writes tight, concrete sentences and hates filler",
    "a working professional explaining something clearly to a smart colleague over email",
    "a magazine feature writer with a calm, vivid voice who varies rhythm naturally",
    "a knowledgeable subject-matter blogger who writes in a relaxed but precise voice",
    "a careful technical writer who is precise but plain-spoken and never pads",
    "an experienced essayist who mixes short punchy lines with longer winding ones",
]

_SCI_PERSONAS = [
    "a senior scientist and lead author who writes concise, active, evidence-driven prose without academic jargon or padding",
    "a seasoned journal editor at Nature or Science who rewrites dense academic drafts into crisp, punchy, direct human prose",
    "an expert academic author who uses natural sentence rhythm (mixing 6-12 word observations with 14-22 word explanations) and avoids robotic nominalizations",
    "a distinguished professor explaining key research findings with direct clarity, active verbs, and zero fluff",
    "a technical specialist writing an authoritative, objective manuscript with precise domain terms and natural human cadence",
]

_BANNED_WORDS = (
    "delve, delves, delving, leverage, leverages, leveraging, foster, fosters, fostering, "
    "harness, harnessing, transformative, revolutionize, revolutionise, pivotal, robust, "
    "seamless, seamlessly, nuanced, multifaceted, holistic, realm, landscape, tapestry, "
    "underscore, underscores, showcasing, showcase, testament, comprehensive, facilitate, "
    "facilitates, demonstrate, demonstrates, utilize, utilizes, utilise, moreover, "
    "furthermore, ultimately, notably, indeed, paramount, crucial, vital, intricate, "
    "myriad, plethora, bolster, navigate, navigating, empower, streamline, cutting-edge, "
    "game-changer, unprecedented, ever-evolving, array of variables, empirical foundations, "
    "cumulative advancements, interdisciplinary collaboration, human emergence, "
    "bears the designation, within human experience"
)

_BANNED_PHRASES = (
    '"It is important to note", "it is worth noting", "plays a crucial/pivotal/key role", '
    '"in today\'s world", "in the modern era", "as we navigate", "when it comes to", '
    '"a testament to", "rich tapestry", "in the realm of", "in the landscape of", '
    '"bears the designation of", "within human experience", "array of external variables"'
)

_WRAPPER_LEAD = re.compile(
    r"^\s*(?:```[a-z]*\s*)?(?:sure[,!.]?\s*|certainly[,!.]?\s*|of course[,!.]?\s*)?"
    r"(?:here(?:'s| is| are)[^:\n]*:\s*|"
    r"(?:the\s+)?(?:rewritten|revised|humaniz(?:ed|er)|edited|new)\s+"
    r"(?:text|paragraph|version|line)[^:\n]*:\s*)",
    re.IGNORECASE,
)

_AI_TELL_SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgame[- ]?changers?\b", re.I), "big shift"),
    (re.compile(r"\bfundamentally\b", re.I), "basically"),
    (re.compile(r"\bstreamlin(e|es|ing|ed)\b", re.I), "simplify"),
    (re.compile(r"\bleverag(e|es|ing|ed)\b", re.I), "use"),
    (re.compile(r"\bharness(es|ing|ed)?\b", re.I), "use"),
    (re.compile(r"\bdelv(e|es|ing|ed)\s+into\b", re.I), "look into"),
    (re.compile(r"\bfacilitat(e|es|ing|ed)\b", re.I), "help"),
    (re.compile(r"\butiliz(e|es|ing|ed)\b", re.I), "use"),
    (re.compile(r"\butilis(e|es|ing|ed)\b", re.I), "use"),
    (re.compile(r"\brevolutioniz(e|es|ing|ed)\b", re.I), "reshape"),
    (re.compile(r"\btransformative\b", re.I), "major"),
    (re.compile(r"\bunderscor(e|es|ing|ed)\b", re.I), "highlight"),
    (re.compile(r"\bshowcas(e|es|ing|ed)\b", re.I), "show"),
    (re.compile(r"\bseamless(ly)?\b", re.I), "smooth"),
    (re.compile(r"\bmultifaceted\b", re.I), "many-sided"),
    (re.compile(r"\bpivotal\b", re.I), "key"),
    (re.compile(r"\bbolster(s|ing|ed)?\b", re.I), "strengthen"),
    (re.compile(r"\bempower(s|ing|ed)?\b", re.I), "enable"),
    (re.compile(r"\bmeticulous(ly)?\b", re.I), "careful"),
    (re.compile(r"\bin today'?s (?:fast-paced|digital|modern) world\b", re.I), "now"),
    (re.compile(r"\bit is important to note that\b", re.I), ""),
    (re.compile(r"\bit is worth noting that\b", re.I), ""),
    (re.compile(r"\bplays? a (?:crucial|pivotal|vital|key) role\b", re.I), "matters"),
]

_DESLANG: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsuper\s+(?=\w)", re.I), "highly "),
    (re.compile(r"\breally\s+(?=changing|important|good|big|hard|nice|great)\b", re.I), ""),
    (re.compile(r"\bhuge\s+amounts?\b", re.I), "vast amounts"),
    (re.compile(r"\bhuge\s+promise\b", re.I), "real promise"),
    (re.compile(r"\bhuge\b", re.I), "great"),
    (re.compile(r"\bawesome\b", re.I), "impressive"),
    (re.compile(r"\bfolks\b", re.I), "people"),
    (re.compile(r"\bmaking waves\b", re.I), "making an impact"),
]


_COT_TELLS = [
    "<think", "</think", "<thought", "</thought", "[think]", "[/think]",
    "thinking process", "analyze user input", "mental iteration",
    "deconstruct original", "constraint check", "burstiness #",
    "draft - mental", "recount carefully", "check against constraints",
    "check constraints", "here's a thinking process", "here is a thinking process",
    "analyze original text", "rewrite idea", "refined draft",
]


def _is_thinking_trace(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(t in lower for t in _COT_TELLS)


def _strip_wrappers(text: str) -> str:
    if not text:
        return ""

    # 0. Handle compound/reasoning models with "**Reasoning** ... **Answer**" blocks
    if re.search(r"\*\*(?:reasoning|thought|thinking)\*\*.*?\*\*(?:answer|response|output|rewritten text)\*\*", text, flags=re.DOTALL | re.IGNORECASE):
        text = re.sub(r".*?\*\*(?:answer|response|output|rewritten text)\*\*[:\s]*", "", text, flags=re.DOTALL | re.IGNORECASE)
    elif re.search(r"^(?:reasoning|thought|thinking):.*?\n+(?:answer|response|output|rewritten text):", text, flags=re.DOTALL | re.IGNORECASE):
        text = re.sub(r".*?\n+(?:answer|response|output|rewritten text):[:\s]*", "", text, flags=re.DOTALL | re.IGNORECASE)

    # 1. Strip closed XML thinking & reasoning tags
    text = re.sub(r"<(?:think|thought|reasoning|thought_process)>.*?</(?:think|thought|reasoning|thought_process)>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\[(?:think|thought|reasoning)\].*?\[/(?:think|thought|reasoning)\]", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```(?:thought|thinking|think|reasoning)\b.*?```", "", text, flags=re.DOTALL | re.IGNORECASE)

    # 2. Strip unclosed thinking tags (if model was truncated or omitted closing tag)
    text = re.sub(r"<(?:think|thought|reasoning|thought_process)>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\[(?:think|thought|reasoning)\].*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```(?:thought|thinking|think|reasoning)\b.*$", "", text, flags=re.DOTALL | re.IGNORECASE)

    # 3. Strip dangling closing tags
    text = re.sub(r"</(?:think|thought|reasoning|thought_process)>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[/(?:think|thought|reasoning)\]", "", text, flags=re.IGNORECASE)

    # 4. Strip markdown reasoning/analysis headers and structured CoT blocks
    cot_patterns = [
        r"^(?:here(?:'s| is) a thinking process:?|thinking process:?).*?(?=\n\n|\n[A-Z0-9]|\Z)",
        r"^\s*(?:\d+\.\s*)?\*\*(?:Analyze User Input|Analyze Original Text|Deconstruct Original Sentence|Draft|Mental Iteration|Constraint Check|Burstiness|Sentence \d|Rewrite idea|Check against constraints|Recount carefully|Apply Constraints|Verify|Reasoning).*?\*\*.*?(?=\n\n|\n[A-Z0-9]|\Z)",
        r"^\s*\*(?:Constraint Check|Burstiness|Sentence \d|Rewrite idea|Check|Draft \d|Refined Draft|Check constraints|Reasoning):\*.*?(?=\n\n|\n[A-Z0-9]|\Z)",
    ]
    for cp in cot_patterns:
        text = re.sub(cp, "", text, flags=re.DOTALL | re.IGNORECASE | re.MULTILINE)

    # 5. Iteratively strip leading conversational preambles
    prev = None
    while prev != text:
        prev = text
        text = _WRAPPER_LEAD.sub("", text).strip()

    # 6. Strip code block fences
    text = re.sub(r"^\s*```[a-z]*\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)

    # 7. Strip outer wrapping quotes if matched
    text = text.strip()
    if len(text) >= 2 and text[0] in "\"'“”" and text[-1] in "\"'“”":
        inner = text[1:-1].strip()
        if inner.count('"') == 0 or inner.count('“') == 0:
            text = inner

    # 8. Filter out any remaining pure CoT lines
    meta_prefixes = [
        "here's a thinking", "here is a thinking", "thinking process",
        "analyze user", "analyze original", "deconstruct original",
        "constraint check", "burstiness", "sentence 1 (", "sentence 2 (",
        "sentence 3 (", "rewrite idea", "refined draft", "draft 1", "draft 2",
        "draft 3", "check against", "check constraints", "recount carefully",
        "apply constraints", "mental iteration", "check facts", "check meaning",
        "register:", "vocabulary:", "banned words:", "target text:", "context before:",
        "context after:", "original sentence:", "target sentence:", "preceding:", "following:",
    ]
    clean_lines = []
    for ln in text.splitlines():
        ln_s = ln.strip()
        if not ln_s:
            clean_lines.append("")
            continue
        cleaned_s = re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", ln_s.lower()).strip()
        cleaned_s = re.sub(r"^\*\*|\*\*$", "", cleaned_s).strip()
        cleaned_s = re.sub(r"^\*|\*$", "", cleaned_s).strip()
        if any(cleaned_s.startswith(p) for p in meta_prefixes):
            continue
        clean_lines.append(ln)

    text = "\n".join(clean_lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)

    # If resulting text still contains raw thinking traces, reject it
    if _is_thinking_trace(text):
        return ""

    return text.strip()


def _deslang(text: str) -> str:
    for pat, repl in _DESLANG:
        def sub(m: re.Match, r: str = repl) -> str:
            g = m.group(0)
            if r and g[:1].isupper():
                return r[:1].upper() + r[1:]
            return r
        text = pat.sub(sub, text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _strip_ai_tells(text: str) -> str:
    for pat, repl in _AI_TELL_SUBS:
        def sub(m: re.Match, r: str = repl) -> str:
            g = m.group(0)
            if r and g[:1].isupper():
                return r[:1].upper() + r[1:]
            return r
        text = pat.sub(sub, text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return text.strip()


def _inflect_ing_to_3sg(verb_ing: str) -> str:
    """Accurately convert a present participle (-ing) to 3rd person singular present tense."""
    v = verb_ing.lower()
    _MAP = {
        "having": "has", "being": "is", "improving": "improves", "reducing": "reduces",
        "increasing": "increases", "enhancing": "enhances", "enabling": "enables",
        "providing": "provides", "creating": "creates", "generating": "generates",
        "producing": "produces", "facilitating": "facilitates", "demonstrating": "demonstrates",
        "indicating": "indicates", "promoting": "promotes", "ensuring": "ensures",
        "accelerating": "accelerates", "yielding": "yields", "leading": "leads",
        "showing": "shows", "helping": "helps", "making": "makes", "giving": "gives",
        "taking": "takes", "changing": "changes", "causing": "causes", "limiting": "limits",
        "mitigating": "mitigates", "optimizing": "optimizes", "optimising": "optimises",
        "amplifying": "amplifies", "identifying": "identifies", "modifying": "modifies",
        "satisfying": "satisfies", "underlying": "underlies", "affecting": "affects",
        "influencing": "influences", "preventing": "prevents", "allowing": "allows",
        "supporting": "supports", "requiring": "requires", "establishing": "establishes",
        "extending": "extends", "expanding": "expands", "fostering": "fosters",
        "strengthening": "strengthens", "lowering": "lowers", "raising": "raises",
        "elevating": "elevates", "driving": "drives", "reshaping": "reshapes",
        "transforming": "transforms", "shifting": "shifts", "opening": "opens",
        "paving": "paves", "resulting": "results", "serving": "serves", "stopping": "stops",
    }
    if v in _MAP:
        res = _MAP[v]
        return res.capitalize() if verb_ing[:1].isupper() else res

    if v.endswith("ying"):
        res = v[:-4] + "ies"
        return res.capitalize() if verb_ing[:1].isupper() else res

    if v.endswith("ing"):
        stem = v[:-3]
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "lsz":
            stem = stem[:-1]
            res = stem + "s"
            return res.capitalize() if verb_ing[:1].isupper() else res

        try:
            from nltk.corpus import wordnet
            if wordnet.synsets(stem + "e", pos=wordnet.VERB):
                res = stem + "es"
                return res.capitalize() if verb_ing[:1].isupper() else res
            if wordnet.synsets(stem, pos=wordnet.VERB):
                res = (stem + "es") if stem.endswith(("s", "sh", "ch", "x", "z", "o")) else (stem + "s")
                return res.capitalize() if verb_ing[:1].isupper() else res
        except Exception:
            pass

        if stem.endswith(("s", "sh", "ch", "x", "z", "o")) or stem.endswith(("c", "v", "z", "bl", "pl", "cl", "gl", "tl")):
            res = stem + "es"
        else:
            res = stem + "s"
        return res.capitalize() if verb_ing[:1].isupper() else res

    return verb_ing + "s"


def _guard_fidelity(src: str, out: str) -> str:
    # 1. Strip hallucinated dummy citation tags like [citation], [ref], [source]
    out = re.sub(r"\[(?:citation|ref|reference|source|cite|insert\s+citation)\]", "", out, flags=re.IGNORECASE)

    # 2. Strict citation retention: only preserve citations that actually existed in src
    src_cites = set(re.findall(r"\[[a-zA-Z0-9,\-\s]{1,20}\]", src))
    out_cites = re.findall(r"\[[a-zA-Z0-9,\-\s]{1,20}\]", out)
    for c in out_cites:
        # Never strip run or lock placeholders
        if not re.match(r"^__(?:RUN|LOCK)_", c) and c not in src_cites:
            out = out.replace(c, "")

    # 3. URL fidelity: if src has no URLs, reject any fabricated URLs
    src_urls = set(re.findall(r"https?://\S+|www\.\S+", src))
    if not src_urls:
        out = re.sub(r"\(?\b(?:https?://|www\.)[^\s()]+(?:\([^\s()]+\)[^\s()]*)*\)?", "", out)

    # 4. Remove consecutive duplicated sentences
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", out) if s.strip()]
    deduped = []
    for s in sents:
        if not deduped or s.lower() != deduped[-1].lower():
            deduped.append(s)
    out = " ".join(deduped)

    # 5. Clean whitespace & punctuation
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def _break_ai_syntactic_patterns(text: str) -> str:
    """Break the #1 AI tell that GPTZero/ZeroGPT detect: trailing participial chains and swollen sentences."""
    if not text:
        return ""

    # 1. Break trailing participial chains into active main clauses using proper verb inflection
    def _sub_thereby(m: re.Match) -> str:
        verb_ing = m.group(1) + "ing"
        verb_3sg = _inflect_ing_to_3sg(verb_ing)
        return f". This {verb_3sg}"

    text = re.sub(r"(?:,\s*|\s*,?\s*and\s+)(?:thereby|thus)\s+(\w+)ing\b", _sub_thereby, text, flags=re.IGNORECASE)
    text = re.sub(r",\s*leading to\b", r". This leads to", text, flags=re.IGNORECASE)
    text = re.sub(r",\s*prompting\b", r". This prompts", text, flags=re.IGNORECASE)
    text = re.sub(r",\s*fostering\b", r" and fosters", text, flags=re.IGNORECASE)
    text = re.sub(r",\s*producing\b", r". This produces", text, flags=re.IGNORECASE)
    text = re.sub(r",\s*ensuring that\b", r". This ensures that", text, flags=re.IGNORECASE)
    text = re.sub(r",\s*refining\b", r" and refines", text, flags=re.IGNORECASE)
    text = re.sub(r",\s*encompassing\b", r", including", text, flags=re.IGNORECASE)

    # 2. Fix specific swollen AI academic phrases
    text = re.sub(r"\bbears? the designation of\b", "is called", text, flags=re.IGNORECASE)
    text = re.sub(r"\bfrom the earliest moments of human emergence\b", "Since early history", text, flags=re.IGNORECASE)
    text = re.sub(r"\ba constantly evolving array of external variables\b", "changing surroundings", text, flags=re.IGNORECASE)
    text = re.sub(r"\bwithin human experience\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bcumulative advancements across disciplines\b", "steady scientific progress", text, flags=re.IGNORECASE)
    text = re.sub(r"\binterdisciplinary collaboration\b", "collaborative research", text, flags=re.IGNORECASE)

    # Clean punctuation & double spaces
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _clean_rewrite(src: str, out: str) -> str:
    out = _strip_wrappers(out)
    if not out:
        return ""
    out = _deslang(out)
    out = _strip_ai_tells(out)
    out = _break_ai_syntactic_patterns(out)
    return _guard_fidelity(src, out)


def _avoid_block(avoid_smooth: Optional[list[str]]) -> str:
    if not avoid_smooth:
        return ""
    picks = [s.strip() for s in avoid_smooth if s.strip()][:3]
    if not picks:
        return ""
    bullets = "\n".join(f'   - "{s[:160]}"' for s in picks)
    return (
        "\n\nSTILL TOO PREDICTABLE (a previous attempt left these reading machine-smooth — "
        "recast THESE hardest of all: different clause order, more specific word choices, "
        "a sharper rhythm, while keeping every fact identical):\n" + bullets
    )


def build_stealth_prompt(
    text: str,
    persona: str,
    is_short: bool,
    avoid_smooth: Optional[list[str]] = None,
    scientific: bool = False,
) -> str:
    if is_short:
        return (
            f"Rewrite this short line (a title or heading) as {persona} would phrase it: "
            f"{'formal and precise' if scientific else 'plain, direct, no hype'}. Keep every "
            f"fact, name, number, and any bracketed reference markers exactly. Add nothing "
            f"that isn't there. Keep it about the same length. Avoid these words: {_BANNED_WORDS}. "
            f"Preserve any tags like <b id='X'>...</b> or __RUN_LOCKED_X__ exactly. "
            f"CRITICAL: Output ONLY the final rewritten line. Do NOT output any thinking, notes, or preamble.\n\nLINE:\n{text}"
        )
    if scientific:
        register = (
            "4. Formal academic register: NO contractions, no casual asides, no slang. "
            "Use exact domain terminology and keep specialized nouns intact. Use active subject-verb constructions "
            "('humans study', 'researchers test', 'substances transform') rather than heavy passive nominalizations "
            "('systematic inquiry was conducted', 'bears the designation of', 'the systematization of accrued findings'). "
            "Keep any inline tags such as <b id='X'>...</b> or <i id='Y'>...</i> around the corresponding terms."
        )
        length_line = (
            "1. SENTENCE BURSTINESS & RHYTHM (CRITICAL): Vary sentence lengths widely to achieve high burstiness. "
            "Alternate short, punchy statements (5-9 words) with longer analytical explanations (20-30 words). "
            "Never make consecutive sentences uniform in length."
        )
        anti_chain = (
            "5. NO PARTICIPIAL CHAINS: Do NOT end sentences with participial tails like ', leading to...', ', thereby enabling...', "
            "', prompting...', ', producing...', or ', fostering...'. Use active main clauses and standard coordinating conjunctions ('and', 'yet', 'because', 'so') instead."
        )
        closing = "Keep the SAME meaning and concise length, formal and scientific without padding"
    else:
        register = "4. Contractions and active voice, professional register. No slang or hype. Keep any inline tags."
        length_line = "1. SENTENCE BURSTINESS & RHYTHM (CRITICAL): Alternate very short punchy sentences (4-8 words) with longer compound sentences (20-32 words). Vary rhythm naturally."
        anti_chain = "5. Avoid participial dangling clauses."
        closing = "Same length and meaning"

    voice = f"Rewrite the TEXT as if {persona} wrote it by hand — genuinely human, which completely defeats AI detectors like GPTZero and ZeroGPT."

    return f"""{voice}

FACTS (non-negotiable): keep every claim, number, name, date, equation, URL, and bracketed citation EXACTLY. Introduce NO new facts, studies, surveys, equations, URLs, or claims not in the TEXT. NEVER output placeholder tokens like '[citation]' or '[ref]'. Keep all technical terms precise. Preserve any tags of the form <b id='...'>...</b>, <i id='...'>...</i>, or __RUN_LOCKED_...__ intact.

HOW:
{length_line}
2. Vary sentence openings; avoid starting consecutive sentences with 'The', 'This', or 'It is'.
3. Plain, precise vocabulary. Never use: {_BANNED_WORDS}. Avoid stock phrases: {_BANNED_PHRASES}.
{register}
{anti_chain}{_avoid_block(avoid_smooth)}

CRITICAL OUTPUT RULES:
- Return ONLY the final rewritten text, one space between sentences.
- Do NOT output any thinking steps, chain-of-thought, reasoning tags, internal analysis, or preambles.
- Do NOT wrap output in markdown code blocks or quotes.

{closing}. TEXT:
{text}"""


def _smooth_hints(variant: str, max_hints: int = 3) -> list[str]:
    try:
        smooth = smooth_sentences(variant, threshold=33.0)
        return [s for _, s, _ in smooth[:max_hints]]
    except Exception:
        return []


def _roughen_smooth_sentences(text: str, cfg: HumanizerConfig) -> str:
    """Refines sentences that remain statistically smooth with strict validation."""
    try:
        smooth = smooth_sentences(text, threshold=33.0)
        if not smooth:
            return text
        sents = split_sentences(text)
        if not sents:
            return text
        personas = _SCI_PERSONAS if cfg.scientific else _PERSONAS
        changed = False

        for idx, sent, ppl in smooth[:3]:
            if idx >= len(sents):
                continue
            persona = cfg.rng.choice(personas)
            prev_ctx = sents[idx - 1] if idx > 0 else ""
            next_ctx = sents[idx + 1] if idx + 1 < len(sents) else ""
            ctx_note = f"Context: Preceded by '{prev_ctx[:80]}...' and followed by '{next_ctx[:80]}...'\n" if prev_ctx or next_ctx else ""

            prompt = (
                f"Rewrite this single sentence to sound genuinely human and unpredictable while keeping identical meaning.\n"
                f"Voice/Style: {persona} ({'formal academic' if cfg.scientific else 'direct and professional'}).\n"
                f"{ctx_note}"
                f"RULES:\n"
                f"1. Output strictly ONE single sentence ending in a period.\n"
                f"2. Keep EVERY fact, number, citation marker, and technical term exactly.\n"
                f"3. Do NOT output any thinking, analysis, reasoning steps, or conversational preamble.\n"
                f"4. Return ONLY the rewritten sentence.\n\n"
                f"SENTENCE:\n{sent}"
            )
            try:
                raw = call_llm_pool(prompt, cfg, temperature=1.0)
                cand = _clean_rewrite(sent, raw)
                if not cand or cand == sent:
                    continue

                # Safety checks on candidate sentence:
                if _is_thinking_trace(cand):
                    continue

                cand_sents = split_sentences(cand)
                if len(cand_sents) > 1:
                    cand = cand_sents[0].strip()

                w_orig = len(sent.split())
                w_cand = len(cand.split())
                # Discard if length exploded or collapsed unreasonably
                if w_cand < max(3, int(w_orig * 0.45)) or w_cand > max(35, int(w_orig * 1.85)):
                    continue

                # Discard if citation or lock fidelity violated
                src_cites = set(re.findall(r"\[[a-zA-Z0-9,\-\s]{1,30}\]", sent))
                cand_cites = set(re.findall(r"\[[a-zA-Z0-9,\-\s]{1,30}\]", cand))
                if src_cites != cand_cites:
                    continue

                src_locks = set(re.findall(r"__(?:LOCK|RUN)_[A-Z0-9_]+__", sent))
                cand_locks = set(re.findall(r"__(?:LOCK|RUN)_[A-Z0-9_]+__", cand))
                if src_locks != cand_locks:
                    continue

                sents[idx] = cand
                changed = True
            except Exception:
                continue

        return " ".join(sents) if changed else text
    except Exception:
        return text


def humanize_paragraph_stealth(text: str, cfg: HumanizerConfig) -> str:
    """Persona-driven LLM rewrite with acceptance loop and guided feedback."""
    # Repair broken unicode before any processing
    text = _fix_encoding(text)
    stripped = text.strip()
    if not stripped:
        return text

    is_short = len(stripped.split()) < 15
    budget = 1 if cfg.stealth_level <= 1 or is_short else max(1, cfg.retry_budget)
    personas = _SCI_PERSONAS if cfg.scientific else _PERSONAS
    persona_i = cfg.rng.randrange(len(personas))

    best_text = text
    best_score = 100.0
    hints: list[str] = []

    for attempt in range(budget):
        if cfg.cancel_event and cfg.cancel_event.is_set():
            break
        persona = personas[(persona_i + attempt) % len(personas)]
        temp = min(1.20, cfg.gemini_temperature + 0.05 * attempt)
        prompt = build_stealth_prompt(text, persona, is_short, avoid_smooth=hints, scientific=cfg.scientific)

        try:
            raw = call_llm_pool(prompt, cfg, temp)
            cand = _clean_rewrite(text, raw)
        except Exception as e:
            logger.warning(f"Stealth rewrite call failed: {e}")
            continue

        if not cand or _is_thinking_trace(cand):
            continue

        # Length guard: do not accept extreme shrinkage or massive inflation
        cand_words = len(cand.split())
        text_words = len(text.split())
        if not is_short:
            if cand_words < int(cfg.min_length_ratio * text_words) or cand_words > int(1.55 * text_words):
                continue

        # Semantic fidelity guard: reject rewrites that lost/distorted content
        if not is_short and not _is_faithful(text, cand):
            logger.debug("Candidate rejected by fidelity guard (content drift).")
            continue

        if budget == 1:
            return cand

        # Score candidate
        res = score_text(cand)
        score = res.get("ai_score", 100.0)

        if score < best_score:
            best_score = score
            best_text = cand

        if best_score <= cfg.target_pct:
            break

        hints = _smooth_hints(best_text)

    if not is_short and cfg.stealth_level >= 2 and best_text != text:
        best_text = _roughen_smooth_sentences(best_text, cfg)

    return best_text


def humanize_paragraph_rules(text: str, cfg: HumanizerConfig) -> str:
    """Fast offline rule pipeline."""
    # Repair broken unicode before any processing
    text = _fix_encoding(text)
    if not text.strip():
        return text
    text, vault = lock_elements(text)
    text = apply_phrase_subs(text, cfg)
    text = apply_word_subs(text, cfg)
    text = apply_contractions(text, cfg)
    text = fix_grammar(text)
    text = unlock_elements(text, vault)
    return text


def humanize_text(text: str, cfg: HumanizerConfig) -> str:
    """Humanize a multi-paragraph text block with live progress reporting."""
    blocks = text.split("\n\n")
    non_empty_indices = [i for i, b in enumerate(blocks) if b.strip()]
    if not non_empty_indices:
        return text

    total_paras = len(non_empty_indices)
    cfg.report_progress(0, total_paras, "Starting text humanization...")

    sid = Checkpoint.compute_id(text, cfg.to_dict())
    ckpt = Checkpoint(sid, text, cfg.to_dict(), total=total_paras, mode="text")

    new_blocks = list(blocks)
    workers = max(1, min(cfg.parallel_workers, total_paras))

    def _work(idx: int) -> tuple[int, str]:
        if cfg.cancel_event and cfg.cancel_event.is_set():
            return idx, blocks[idx]
        cached = ckpt.get(idx)
        if cached is not None:
            return idx, cached
        b = blocks[idx]
        if cfg.use_gemini:
            res = humanize_paragraph_stealth(b, cfg)
        else:
            res = humanize_paragraph_rules(b, cfg)
        ckpt.put(idx, res)
        return idx, res

    done_count = 0
    if workers == 1:
        for idx in non_empty_indices:
            if cfg.cancel_event and cfg.cancel_event.is_set():
                break
            _, new_blocks[idx] = _work(idx)
            done_count += 1
            cfg.report_progress(done_count, total_paras, f"Paragraph {done_count}/{total_paras} humanized")
    else:
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_work, idx): idx for idx in non_empty_indices}
            for fut in cf.as_completed(futures):
                if cfg.cancel_event and cfg.cancel_event.is_set():
                    for f in futures:
                        f.cancel()
                    break
                idx, rewritten = fut.result()
                new_blocks[idx] = rewritten
                done_count += 1
                cfg.report_progress(done_count, total_paras, f"Paragraph {done_count}/{total_paras} humanized")

    if not (cfg.cancel_event and cfg.cancel_event.is_set()):
        ckpt.clear()
        cfg.report_progress(total_paras, total_paras, "Text humanization complete")

    return "\n\n".join(new_blocks)


def humanize_docx(src: Path, cfg: HumanizerConfig, dst: Optional[Path] = None) -> Path:
    """Humanize a Word Document preserving run-level formatting with parallel execution & progress reporting."""
    doc = Document(str(src))
    target_dst = dst or src.with_name(f"{src.stem}_humanized{src.suffix}")

    paras_to_process = []
    for p in iter_document_paragraphs(doc):
        if not should_skip_paragraph(p):
            paras_to_process.append(p)

    if not paras_to_process:
        doc.save(str(target_dst))
        cfg.report_progress(1, 1, "Document empty or skipped, saved directly.")
        return target_dst

    total_paras = len(paras_to_process)
    cfg.report_progress(0, total_paras, "Preparing document runs...")

    # Build checkpoint key based on document paragraphs
    doc_hash_text = "\n\n".join(p.text for p in paras_to_process)
    sid = Checkpoint.compute_id(doc_hash_text, cfg.to_dict())
    ckpt = Checkpoint(sid, doc_hash_text, cfg.to_dict(), total=total_paras, mode="docx", source_path=str(src))

    workers = max(1, min(cfg.parallel_workers, total_paras))

    # Store vaults keyed by paragraph index so reconstruction uses the
    # same tag→style mapping that was produced during the forward pass.
    vault_map: dict[int, dict] = {}
    vault_lock = threading.Lock()

    def _process_para(idx: int) -> tuple[int, str]:
        if cfg.cancel_event and cfg.cancel_event.is_set():
            return idx, paras_to_process[idx].text
        cached = ckpt.get(idx)
        p = paras_to_process[idx]
        tagged_text, vault = _tag_runs(p)
        with vault_lock:
            vault_map[idx] = vault
        if cached is not None:
            return idx, cached
        if not tagged_text.strip():
            return idx, p.text

        if cfg.use_gemini:
            rewritten = humanize_paragraph_stealth(tagged_text, cfg)
        else:
            rewritten = humanize_paragraph_rules(tagged_text, cfg)

        ckpt.put(idx, rewritten)
        return idx, rewritten

    results_map: dict[int, str] = {}
    done_count = 0
    if workers == 1:
        for idx in range(total_paras):
            if cfg.cancel_event and cfg.cancel_event.is_set():
                break
            _, res_txt = _process_para(idx)
            results_map[idx] = res_txt
            done_count += 1
            cfg.report_progress(done_count, total_paras, f"DOCX paragraph {done_count}/{total_paras} humanized")
    else:
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_process_para, idx): idx for idx in range(total_paras)}
            for fut in cf.as_completed(futures):
                if cfg.cancel_event and cfg.cancel_event.is_set():
                    for f in futures:
                        f.cancel()
                    break
                idx, rewritten = fut.result()
                results_map[idx] = rewritten
                done_count += 1
                cfg.report_progress(done_count, total_paras, f"DOCX paragraph {done_count}/{total_paras} humanized")

    # Reconstruct all paragraphs in order using the vaults captured during the
    # forward pass — NOT a second _tag_runs call on (now-modified) paragraphs.
    cfg.report_progress(total_paras, total_paras, "Reconstructing run-level styles and formatting...")
    for idx, p in enumerate(paras_to_process):
        if idx in results_map:
            base_style = _extract_run_style(p.runs[0]) if p.runs else RunStyle()
            vault = vault_map.get(idx, {})
            _reconstruct_runs(p, results_map[idx], vault, base_style)

    if not (cfg.cancel_event and cfg.cancel_event.is_set()):
        ckpt.clear()
        cfg.report_progress(total_paras, total_paras, "DOCX humanization complete")

    doc.save(str(target_dst))
    return target_dst
