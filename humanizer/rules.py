"""
Offline rule-based rewriter.

Removes the patterns catalogued in :mod:`humanizer.patterns` without any LLM:

* inflection-aware vocabulary swaps (``leveraging`` -> ``using``, never ``use``),
* phrase rewrites (filler, inflated significance, copula avoidance, hedges),
* sentence-level repairs (formulaic transition openers, shallow ``-ing`` tails,
  ``not only ... but also``, em-dash overuse, chatbot residue),
* rhythm shaping (splitting over-long compound sentences for burstiness),
* optional contractions (general register only), and
* grammar clean-up (a/an, doubled words, spacing, capitalisation).

Protected content — citations, numbers with units, statistics, equations,
URLs, DOIs and DOCX formatting tags — is locked before any rewrite and
restored afterwards, so it can never be altered.

Domain terms are respected: in the academic register nothing that is also a
technical term (catalyst, spectrum, yield, significant, novel, robust ...)
is touched.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Callable, Optional

from humanizer.config import HumanizerConfig
from humanizer.text import capitalize_sentences, conjugate, ing_to_3sg, match_case, split_sentences

logger = logging.getLogger(__name__)

ALL = "all"          # applied in every register
GENERAL = "general"  # applied only when cfg.scientific is False


# ---------------------------------------------------------------------------
# Locking of protected elements
# ---------------------------------------------------------------------------
_LOCK_RE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"__RUN_(?:LOCKED|PLACEHOLDER)_\d+__"), "PLACEHOLDER"),
    (re.compile(r"</?(?:bi|b|i|u)(?:\s+id=['\"]\d+['\"])?>", re.IGNORECASE), "TAG"),
    (re.compile(r"https?://[^\s<>\"]+[^\s<>\".,;:!?)\]]"), "URL"),
    (re.compile(r"www\.[^\s<>\"]+[^\s<>\".,;:!?)\]]"), "URL"),
    (re.compile(r"\bdoi:\s*10\.\S+[^\s.,;]", re.IGNORECASE), "DOI"),
    (re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;]"), "DOI"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+\w"), "EMAIL"),
    (re.compile(r"\$\$.+?\$\$|\$[^$\n]+\$", re.DOTALL), "EQUATION"),
    (re.compile(r"\\[a-zA-Z]+(?:\[[^\]]*\])?\{[^}]*\}"), "LATEX"),
    (re.compile(r"`[^`\n]+`"), "CODE"),
    (re.compile(r"\[[0-9]+(?:\s*[,\-–]\s*[0-9]+)*\]"), "CITATION"),
    (re.compile(r"\[[A-Za-z][A-Za-z0-9 .,&;:\-]{0,40}(?:19|20)\d{2}[a-z]?\]"), "CITATION"),
    (re.compile(r"\((?:[A-Z][A-Za-z'’\-]+(?: et al\.?| and [A-Z][A-Za-z'’\-]+| & [A-Z][A-Za-z'’\-]+)?,? (?:19|20)\d{2}[a-z]?(?:[;,] ?)?)+\)"), "CITATION"),
    (re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ]+"), "SUP_SUB"),
    (re.compile(r"\b(?:p|n|r|R|r²|R²|β|α|df|F|t|z|χ²|OR|RR|HR|CI|SD|SE|M)\s*[=<>≤≥]\s*-?[\d.]+%?"), "STAT"),
    (re.compile(
        r"(?<![\w.])[-−±]?\d[\d,]*(?:\.\d+)?(?:\s*(?:±|–|-)\s*\d[\d,]*(?:\.\d+)?)?\s*(?:"
        r"[mµu]?W\s*cm[-−⁻]?[2²]|[mµu]?A\s*cm[-−⁻]?[2²]|[kMGT]?Wh?\s*kg[-−⁻]?[1¹]|J\s*g[-−⁻]?[1¹]|"
        r"%|‰|mg|kg|g|mL|ml|L|μg|µg|μL|µL|mmol|μmol|mol|mM|μM|µM|nM|M|cm|mm|nm|μm|µm|pm|km|m|"
        r"kb|Mb|Gb|kB|MB|GB|TB|mV|kV|V|eV|keV|MeV|GeV|K|°C|°F|kW|mW|MW|W|"
        r"mAh|Ah|mA|A|kWh|Wh|kJ|J|kPa|MPa|GPa|Pa|mbar|bar|Torr|atm|kHz|MHz|GHz|Hz|"
        r"ppm|ppb|rpm|ms|μs|µs|ns|ps|fs|min|hr|h|s|Da|kDa)(?![\w])"
    ), "QTY"),
    (re.compile(r"(?<![\w.])\d[\d,]*\.\d+(?![\w.])"), "NUM"),
]
_TOKEN_RE = re.compile(r"__LOCK_[A-Z_]+_\d+__")


def lock_elements(text: str) -> tuple[str, dict[str, str]]:
    """Replace protected spans with opaque tokens. Returns (locked_text, vault)."""
    vault: dict[str, str] = {}
    if not text:
        return text, vault
    spans: list[tuple[int, int, str]] = []
    for pat, label in _LOCK_RE:
        for m in pat.finditer(text):
            if m.end() > m.start():
                spans.append((m.start(), m.end(), label))
    if not spans:
        return text, vault
    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    merged: list[list] = []
    for s, e, lbl in spans:
        if merged and s < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
            continue
        merged.append([s, e, lbl])
    out = []
    last = 0
    for i, (s, e, lbl) in enumerate(merged):
        tok = f"__LOCK_{lbl}_{i}__"
        vault[tok] = text[s:e]
        out.append(text[last:s])
        out.append(tok)
        last = e
    out.append(text[last:])
    return "".join(out), vault


def unlock_elements(text: str, vault: dict[str, str]) -> str:
    """Restore locked tokens (handles nested tokens via fixed-point expansion)."""
    if not text or not vault:
        return text
    for _ in range(5):
        new = _TOKEN_RE.sub(lambda m: vault.get(m.group(0), m.group(0)), text)
        if new == text:
            break
        text = new
    return text


# ---------------------------------------------------------------------------
# Vocabulary tables
# ---------------------------------------------------------------------------
# Verbs: lemma -> (replacement lemma, register). All inflections are generated.
_VERBS: dict[str, tuple[str, str]] = {
    "leverage": ("use", ALL),
    "utilize": ("use", ALL),
    "utilise": ("use", ALL),
    "underscore": ("stress", ALL),
    "showcase": ("show", ALL),
    "bolster": ("strengthen", ALL),
    "garner": ("gain", ALL),
    "streamline": ("simplify", ALL),
    "empower": ("enable", ALL),
    "spearhead": ("lead", ALL),
    "revolutionize": ("reshape", ALL),
    "revolutionise": ("reshape", ALL),
    "elucidate": ("explain", ALL),
    "necessitate": ("require", ALL),
    "ascertain": ("determine", ALL),
    "facilitate": ("aid", ALL),
    "delve": ("dig", ALL),
    "commence": ("begin", ALL),
    # Plain-English swaps that would flatten an academic register.
    "demonstrate": ("show", GENERAL),
    "highlight": ("show", GENERAL),
    "enhance": ("improve", GENERAL),
    "endeavor": ("try", GENERAL),
    "endeavour": ("try", GENERAL),
    "terminate": ("end", GENERAL),
    "purchase": ("buy", GENERAL),
    "assist": ("help", GENERAL),
    "ensure": ("make sure", GENERAL),
    "comprehend": ("understand", GENERAL),
    "endorse": ("back", GENERAL),
}
_VERB_EXCLUDE_NEXT: dict[str, str] = {
    # Do not swap when the word is used as a noun/adjective in these contexts.
    "leverage": r"(?!\s+(?:ratio|ratios|effect|point|buyout))",
    "showcase": r"(?!\s+(?:of|for)\b)",
    "highlight": r"(?!\s+(?:of|reel)\b)",
    "purchase": r"(?!\s+(?:price|order|agreement|of)\b)",
}

# Adjectives / nouns / adverbs: word -> (replacement, register).
_WORDS: dict[str, tuple[str, str]] = {
    "pivotal": ("central", ALL),
    "crucial": ("important", ALL),
    "crucially": ("importantly", ALL),
    "paramount": ("essential", ALL),
    "seamless": ("smooth", ALL),
    "seamlessly": ("smoothly", ALL),
    "multifaceted": ("complex", ALL),
    "holistic": ("broad", ALL),
    "holistically": ("as a whole", ALL),
    "meticulous": ("careful", ALL),
    "meticulously": ("carefully", ALL),
    "intricacies": ("details", ALL),
    "tapestry": ("mix", ALL),
    "testament": ("proof", ALL),
    "realm": ("field", ALL),
    "realms": ("fields", ALL),
    "interplay": ("interaction", ALL),
    "synergy": ("cooperation", ALL),
    "synergies": ("combined benefits", ALL),
    "plethora": ("abundance", ALL),
    "cornerstone": ("foundation", ALL),
    "bedrock": ("foundation", ALL),
    "linchpin": ("central part", ALL),
    "beacon": ("example", ALL),
    "burgeoning": ("growing", ALL),
    "unwavering": ("steady", ALL),
    "ever-evolving": ("changing", ALL),
    "ever-changing": ("changing", ALL),
    "ever-growing": ("growing", ALL),
    "game-changer": ("major shift", ALL),
    "game-changers": ("major shifts", ALL),
    "game-changing": ("major", ALL),
    "gamechanger": ("major shift", ALL),
    "groundbreaking": ("pioneering", ALL),
    "noteworthy": ("notable", ALL),
    "commendable": ("good", ALL),
    "invaluable": ("very useful", ALL),
    "indispensable": ("essential", ALL),
    "transformative": ("major", ALL),
    "nestled": ("set", ALL),
    "breathtaking": ("striking", ALL),
    "bustling": ("busy", ALL),
    "utilization": ("use", ALL),
    "utilisation": ("use", ALL),
    # General register only (these are legitimate in technical prose).
    "cutting-edge": ("advanced", GENERAL),
    "vibrant": ("lively", GENERAL),
    "intricate": ("complex", GENERAL),
    "robust": ("strong", GENERAL),
    "comprehensive": ("thorough", GENERAL),
    "methodology": ("method", GENERAL),
    "methodologies": ("methods", GENERAL),
    "numerous": ("many", GENERAL),
    "approximately": ("about", GENERAL),
    "individuals": ("people", GENERAL),
    "additionally": ("also", GENERAL),
    "subsequently": ("later", GENERAL),
    "sufficient": ("enough", GENERAL),
    "commencement": ("start", GENERAL),
    "assistance": ("help", GENERAL),
    "endeavor": ("effort", GENERAL),
    "endeavors": ("efforts", GENERAL),
    "endeavours": ("efforts", GENERAL),
    "endeavour": ("effort", GENERAL),
}

# Ordered phrase rewrites: (pattern, replacement or callable, register).
_PhraseRepl = "str | Callable[[re.Match[str]], str]"


def _verb_phrase(lemma_repl: str) -> Callable[[re.Match[str]], str]:
    """Build a callback that re-inflects `lemma_repl` to match the matched verb's form."""
    forms = conjugate(lemma_repl)

    def _cb(m: re.Match[str]) -> str:
        v = m.group("v").lower()
        if v.endswith("ing"):
            out = forms["ing"]
        elif v.endswith("ed"):
            out = forms["past"]
        elif v.endswith("s") and not v.endswith("ss"):
            out = forms["3sg"]
        else:
            out = forms["base"]
        return match_case(m.group(0), out)

    return _cb


_PHRASES: list[tuple[str, object, str]] = [
    # Chatbot residue & knowledge cut-off clauses
    (r"\b(?:as of|up to) my (?:last |latest )?(?:knowledge |training )?(?:update|cutoff|cut-off)(?: in \w+(?: \d{4})?)?,\s*", "", ALL),
    (r"\bas an AI(?: language model)?,\s*", "", ALL),
    (r"^(?:certainly|absolutely|sure|of course|great question)[!.,]\s*", "", ALL),
    # Filler
    (r"(?:^|(?<=[.!?]\s))As (?:we|society|the world|humanity) (?:navigate|navigates|move through|enter|embrace|look to)[^,.;!?]{1,80},\s*(?P<n>\w)",
     lambda m: m.group("n").upper(), ALL),
    (r"\bit is (?:important|essential|crucial|worth) (?:to note|noting|mentioning|to remember|to recognize|to understand|to emphasize|to highlight) that\s+", "", ALL),
    (r"\bit should be (?:noted|emphasized|emphasised|mentioned) that\s+", "", ALL),
    (r"\bit goes without saying that\s+", "", ALL),
    (r"\bit is (?:important|essential|crucial) to (?:note|recognize|understand)\b", "note", ALL),
    (r"\bin today'?s (?:fast-paced|digital|modern|rapidly changing|ever-changing|interconnected) (?:world|age|era|landscape|society)\b", "today", ALL),
    (r"\bin the modern era\b", "today", ALL),
    (r"\bin (?:the|this) day and age\b", "today", ALL),
    (r"\bwhen it comes to\b", "for", ALL),
    (r"\bat the end of the day\b", "in the end", ALL),
    (r"\ba (?:wide|broad|diverse|vast) (?:range|array|variety|spectrum) of\b", "many", ALL),
    (r"\ba (?:multitude|plethora|myriad|host) of\b", "many", ALL),
    (r"\bmyriad\b(?= [a-z]+s\b)", "many", ALL),
    (r"\bdue to the fact that\b", "because", ALL),
    (r"\bin light of the fact that\b", "because", ALL),
    (r"\bowing to the fact that\b", "because", ALL),
    (r"\bdespite the fact that\b", "although", ALL),
    (r"\bfirst and foremost\b", "first", ALL),
    (r"\blast but not least\b", "finally", ALL),
    (r"\bat this point in time\b", "now", ALL),
    (r"\bat the present time\b", "now", ALL),
    (r"\bwith a view to\b", "to", ALL),
    (r"\bin order to\b", "to", ALL),
    (r"\bfor the purpose of\b", "for", ALL),
    (r"\bin the event that\b", "if", ALL),
    (r"\b(?:has|have) the (?:ability|capacity|potential) to\b", "can", ALL),
    (r"\bis able to\b", "can", GENERAL),
    (r"\bare able to\b", "can", GENERAL),
    (r"\bprior to\b", "before", GENERAL),
    (r"\bwith regard to\b|\bwith respect to\b|\bin regard to\b", "about", GENERAL),
    (r"\b(?:let'?s|let us) (?:dive|delve) (?:deep )?in(?:to)?\b[^.!?]*[.!?]\s*", "", ALL),
    (r"\bhere'?s what you need to know[:.]\s*", "", ALL),
    # Inflated significance
    (r"\b(?:stands|serves) as a testament to\b", "shows", ALL),
    (r"\bstood as a testament to\b", "showed", ALL),
    (r"\b(?:is|was|are|were) a testament to\b", lambda m: {"is": "shows", "are": "show", "was": "showed", "were": "showed"}[m.group(0).split()[0].lower()], ALL),
    (r"\ba testament to\b", "evidence of", ALL),
    (r"\bplays? (?:a |an )?(?:crucial|pivotal|vital|key|significant|central|critical|important|instrumental) role in\b",
     lambda m: "is central to" if m.group(0).lower().startswith("plays") else "are central to", ALL),
    (r"\bplayed (?:a |an )?(?:crucial|pivotal|vital|key|significant|central|critical|important|instrumental) role in\b", "was central to", ALL),
    (r"\bplaying (?:a |an )?(?:crucial|pivotal|vital|key|significant|central|critical|important|instrumental) role in\b", "being central to", ALL),
    (r"\b(?P<v>set|sets|setting) the stage for\b", _verb_phrase("prepare"), ALL),
    (r"\b(?P<v>pave|paves|paved|paving) the way for\b", _verb_phrase("lead to"), ALL),
    (r"\b(?P<v>open|opens|opened|opening) up new (?:avenues|possibilities|horizons) for\b", _verb_phrase("create room for"), ALL),
    (r"\b(?:in|across) the (?:ever-evolving |rapidly evolving |evolving |broader |modern |current )?landscape of\b", "in", ALL),
    (r"\bin the realm of\b", "in", ALL),
    (r"\bwithin the realm of\b", "within", ALL),
    (r"\ba (?:rich|vibrant|diverse|complex) tapestry of\b", "a mix of", ALL),
    (r"\ba tapestry of\b", "a mix of", ALL),
    (r"\bof paramount importance\b", "essential", ALL),
    (r"\b(?P<v>unleash|unleashes|unleashed|unleashing) the (?:full )?(?:power|potential) of\b", _verb_phrase("use"), ALL),
    (r"\b(?P<v>harness|harnesses|harnessed|harnessing) the (?:full )?(?:power|potential) of\b", _verb_phrase("use"), ALL),
    (r"\b(?P<v>harness|harnesses|harnessed|harnessing)\b(?=\s+(?:the|its|their|this|these|our|AI|data|solar|wind|renewable)\b)", _verb_phrase("use"), ALL),
    (r"\b(?P<v>navigate|navigates|navigated|navigating) the (?:complexities|challenges|intricacies|complexity) of\b", _verb_phrase("handle"), ALL),
    (r"\b(?P<v>delve|delves|delved|delving) (?:deep(?:er|ly)? )?into\b", _verb_phrase("look into"), ALL),
    (r"\b(?P<v>embark|embarks|embarked|embarking) (?:up)?on\b", _verb_phrase("start"), ALL),
    (r"\b(?P<v>foster|fosters|fostered|fostering)\b(?!\s+(?:care|parent|parents|child|children|home|homes|family|families|mother|father))", _verb_phrase("encourage"), ALL),
    (r"\bdeep dive\b", "close look", ALL),
    (r"\bindelible (?:mark|impact)\b", "lasting effect", ALL),
    (r"\bfundamentally (?=(?:transform|chang|reshap|alter)\w*)", "", ALL),
    # Copula avoidance
    (r"\bserves as (?=(?:a|an|the)\b)", "is ", ALL),
    (r"\bserved as (?=(?:a|an|the)\b)", "was ", ALL),
    (r"\bstands as (?=(?:a|an|the)\b)", "is ", ALL),
    (r"\bboasts\b(?= (?:a|an|the|over|more|some|many|\d))", "has", ALL),
    (r"\bboast\b(?= (?:a|an|the|over|more|some|many|\d))", "have", ALL),
    (r"\bin the heart of\b", "in", ALL),
    (r"\bnestled (?:in|within|among|between)\b", lambda m: m.group(0).split()[1], ALL),
    # Hedging
    (r"\b(could|may|might|can) potentially\b", r"\1", ALL),
    (r"\bit could be argued that\s+", "", ALL),
    (r"\bpossibly (?=(?:could|may|might))", "", ALL),
    # Generic upbeat conclusions
    (r"\bholds? (?:great|immense|tremendous|enormous|significant|considerable) (?:promise|potential)\b",
     lambda m: "shows promise" if m.group(0).lower().startswith("holds") else "show promise", ALL),
    (r"\bthe future (?:looks|is) (?:bright|promising)\b", "the outlook is good", ALL),
]

_TRANSITION_DROP = re.compile(
    r"(?P<lead>^|(?<=[.!?]\s)|(?<=[.!?][\"'”’)]\s)|(?<=\n))(?P<word>Furthermore|Moreover|Additionally|In addition|Notably|Importantly|Ultimately|Indeed|Interestingly|Crucially|Undeniably|Unquestionably|Essentially|In conclusion|In summary|To summarize|To sum up|In essence|All in all|Overall|Consequently|Accordingly|Hence|As such|Thus|Therefore|Nonetheless|Nevertheless|Subsequently|Conversely|Similarly|Likewise),\s+(?P<next>\S)",
)
_TRANSITION_REPLACE: dict[str, tuple[str, ...]] = {
    # word -> alternatives ("" means drop the opener). Academic-safe options only.
    "Furthermore": ("", "", "Also,"), "Moreover": ("", "", "Also,"), "Additionally": ("", "Also,"),
    "In addition": ("", "Also,"), "Notably": ("",), "Importantly": ("",), "Ultimately": ("",),
    "Indeed": ("",), "Interestingly": ("",), "Crucially": ("",), "Undeniably": ("",),
    "Unquestionably": ("",), "Essentially": ("",), "In conclusion": ("",), "In summary": ("",),
    "To summarize": ("",), "To sum up": ("",), "In essence": ("",), "All in all": ("",),
    "Consequently": ("As a result,", "So", ""), "Accordingly": ("So", ""),
    "Subsequently": ("Then", "Later,"),
}
_TRANSITION_KEEP_SCI = {"Overall", "Hence", "As such", "Thus", "Therefore", "Nonetheless",
                        "Nevertheless", "Conversely", "Similarly", "Likewise"}

_TAIL_VERBS = {
    "highlighting": "shows", "underscoring": "shows", "showcasing": "shows",
    "emphasizing": "stresses", "emphasising": "stresses", "reflecting": "reflects",
    "symbolizing": "symbolizes", "symbolising": "symbolises", "fostering": "encourages",
    "reinforcing": "reinforces", "solidifying": "strengthens", "cementing": "strengthens",
    "leading": "leads", "resulting": "results", "contributing": "contributes",
    "prompting": "prompts", "enabling": "enables", "allowing": "allows",
    "making": "makes", "creating": "creates", "producing": "produces",
    "ensuring": "ensures", "offering": "offers", "providing": "provides", "giving": "gives",
    "bringing": "brings", "driving": "drives", "reducing": "reduces", "improving": "improves",
    "increasing": "increases", "opening": "opens", "reshaping": "reshapes", "transforming": "transforms",
    "revolutionizing": "changes", "revolutionising": "changes", "boosting": "boosts", "lowering": "lowers",
}
_TAIL_RE = re.compile(
    r",\s+(?:(?:and\s+)?(?:thereby|thus)\s+)?(?P<verb>highlighting|underscoring|showcasing|emphasizing|emphasising|"
    r"reflecting|symbolizing|symbolising|fostering|reinforcing|solidifying|cementing|"
    r"leading to|resulting in|contributing to|prompting|enabling|allowing|making|creating|producing|ensuring|"
    r"offering|providing|giving|bringing|driving|reducing|improving|increasing|opening|reshaping|transforming|"
    r"revolutionizing|revolutionising|boosting|lowering)\b"
)
_THEREBY_RE = re.compile(r",?\s+(?:and\s+)?(?:thereby|thus)\s+(?P<verb>[a-z]+ing)\b")

_NOT_ONLY_RE = re.compile(r"\bnot only\s+(?P<a>[^.!?;]{1,120}?),?\s+but (?:also\s+)?(?P<b>[^.!?;]{1,160})")
_NOT_JUST_RE = re.compile(
    r"\b(?P<subj>[Ii]t'?s|[Ii]t is|[Tt]his is|[Tt]hat is) not (?:just|merely|simply|only) (?:about )?(?P<a>[^.!?,;]{1,60})[,;] (?:it'?s|it is|but)(?: (?:about|also))? (?P<b>[^.!?]{1,120})"
)

_CHATBOT_SENTENCE = re.compile(
    r"^(?:I hope (?:this|that) (?:helps|is helpful|clarifies)|(?:Please )?(?:let me know|feel free to)|"
    r"Would you like (?:me )?to|(?:Great|Excellent|Good) question|Is there anything else|Happy to help|"
    r"(?:Certainly|Absolutely|Sure)[!.]?$|Here(?:'s| is) (?:a|an|the) (?:rewritten|revised|summary|overview)|"
    r"The future (?:looks|is) (?:bright|promising|exciting)[.!]?$|Only time will tell[.!]?$|The possibilities are (?:endless|limitless)[.!]?$|"
    r"(?:Exciting|Interesting) times (?:lie|are) ahead[.!]?$)",
    re.IGNORECASE,
)
_DETERMINER_START = re.compile(
    r"^(?:the|this|these|that|those|it|they|we|he|she|its|their|our|his|her|such|each|many|most|some|"
    r"several|both|all|one|a|an|there|researchers|scientists|students|people|users)\b",
    re.IGNORECASE,
)


@lru_cache(maxsize=4)
def _compiled_phrases(register: str) -> list[tuple[re.Pattern[str], object]]:
    out = []
    for pat, repl, scope in _PHRASES:
        if scope == GENERAL and register != GENERAL:
            continue
        out.append((re.compile(pat, re.IGNORECASE | re.MULTILINE), repl))
    return out


@lru_cache(maxsize=4)
def _word_table(register: str) -> dict[str, str]:
    """Flattened {surface form (lowercase): replacement} for a register."""
    table: dict[str, str] = {}
    for lemma, (repl, scope) in _VERBS.items():
        if scope == GENERAL and register != GENERAL:
            continue
        src_forms = conjugate(lemma)
        dst_forms = conjugate(repl)
        for key in ("base", "3sg", "past", "ing"):
            table[src_forms[key]] = dst_forms[key]
    for word, (repl, scope) in _WORDS.items():
        if scope == GENERAL and register != GENERAL:
            continue
        table[word] = repl
    return table


@lru_cache(maxsize=4)
def _pp_table(register: str) -> dict[str, str]:
    """{source past form: replacement past participle} where the two differ (showed/shown)."""
    out: dict[str, str] = {}
    for lemma, (repl, scope) in _VERBS.items():
        if scope == GENERAL and register != GENERAL:
            continue
        dst = conjugate(repl)
        if dst["pp"] != dst["past"]:
            out[conjugate(lemma)["past"]] = dst["pp"]
    return out


_PERFECT_BEFORE = re.compile(r"\b(?:has|have|had|been|be|is|are|was|were|being|get|gets|got)\s+(?:\w+ly\s+|not\s+)?$", re.IGNORECASE)


@lru_cache(maxsize=4)
def _word_regex(register: str) -> re.Pattern[str]:
    table = _word_table(register)
    alts = []
    for w in sorted(table, key=len, reverse=True):
        lemma_guard = ""
        for lemma, guard in _VERB_EXCLUDE_NEXT.items():
            if w.startswith(lemma[:-1]):
                lemma_guard = guard
        alts.append(re.escape(w) + lemma_guard)
    return re.compile(r"(?<![\w-])(?:" + "|".join(alts) + r")(?![\w-])", re.IGNORECASE)


def _register(cfg: Optional[HumanizerConfig]) -> str:
    return "academic" if (cfg is None or cfg.scientific) else GENERAL


# Public substitution table (kept for backwards compatibility).
DEFAULT_SUBSTITUTIONS: dict[str, str] = dict(_word_table(GENERAL))

DEFAULT_CONTRACTIONS: dict[str, str] = {
    "are not": "aren't", "cannot": "can't", "could not": "couldn't", "did not": "didn't",
    "does not": "doesn't", "do not": "don't", "had not": "hadn't", "has not": "hasn't",
    "have not": "haven't", "is not": "isn't", "it is": "it's", "should not": "shouldn't",
    "that is": "that's", "there is": "there's", "they are": "they're", "we are": "we're",
    "were not": "weren't", "will not": "won't", "would not": "wouldn't", "you are": "you're",
    "we have": "we've", "they have": "they've", "I am": "I'm", "let us": "let's",
}
# Contractions that are unsafe when followed by these words ("it is clear that it's...").
_CONTRACTION_BLOCK_NEXT = re.compile(r"^\s*(?:not\b|[.,;:!?]|$)")


# ---------------------------------------------------------------------------
# Rewriting passes
# ---------------------------------------------------------------------------
def _apply_callable(rx: re.Pattern[str], repl: object, text: str) -> str:
    if callable(repl):
        return rx.sub(repl, text)  # type: ignore[arg-type]
    rs = str(repl)
    if "\\" in rs:
        return rx.sub(rs, text)
    return rx.sub(lambda m: match_case(m.group(0), rs) if rs else "", text)


def apply_phrase_subs(text: str, cfg: Optional[HumanizerConfig] = None) -> str:
    if cfg is not None and not cfg.use_phrase_subs:
        return text
    for rx, repl in _compiled_phrases(_register(cfg)):
        text = _apply_callable(rx, repl, text)
    return text


def apply_word_subs(text: str, cfg: Optional[HumanizerConfig] = None) -> str:
    register = _register(cfg)
    table = _word_table(register)
    pp = _pp_table(register)
    custom = getattr(cfg, "substitutions", None) if cfg is not None else None
    rng = cfg.rng if cfg is not None else None
    prob = cfg.subs_prob if cfg is not None else 1.0

    def _sub(m: re.Match[str]) -> str:
        w = m.group(0)
        if rng is not None and prob < 1.0 and rng.random() > prob:
            return w
        key = w.lower()
        repl = table.get(key)
        if repl and key in pp and _PERFECT_BEFORE.search(m.string[max(0, m.start() - 30):m.start()]):
            repl = pp[key]
        return match_case(w, repl) if repl else w

    text = _word_regex(register).sub(_sub, text)
    if custom:
        for word, repl in custom.items():
            rx = re.compile(r"(?<![\w-])" + re.escape(word) + r"(?![\w-])", re.IGNORECASE)
            text = rx.sub(lambda m, r=repl: match_case(m.group(0), r), text)
    return text


def apply_contractions(text: str, cfg: Optional[HumanizerConfig] = None) -> str:
    if cfg is None or not cfg.use_contractions or cfg.scientific:
        return text
    for full, short in DEFAULT_CONTRACTIONS.items():
        rx = re.compile(r"\b" + re.escape(full) + r"\b", re.IGNORECASE)

        def _sub(m: re.Match[str], s: str = short) -> str:
            # Avoid stressed/clause-final forms ("that is." / "it is not") — keep those full.
            if _CONTRACTION_BLOCK_NEXT.match(m.string[m.end():]):
                return m.group(0)
            if cfg.rng.random() > cfg.contract_prob:
                return m.group(0)
            return match_case(m.group(0), s)

        text = rx.sub(_sub, text)
    return text


def _cap_first(s: str) -> str:
    for i, ch in enumerate(s):
        if ch.isalpha():
            return s[:i] + ch.upper() + s[i + 1:]
        if not (ch in "\"'“‘([" or ch.isspace()):
            return s
    return s


def drop_transitions(text: str, cfg: Optional[HumanizerConfig] = None) -> str:
    """Remove or vary formulaic sentence-initial transitions ("Furthermore, ...")."""
    sci = cfg is None or cfg.scientific
    rng = cfg.rng if cfg is not None else None

    def _sub(m: re.Match[str]) -> str:
        word = m.group("word")
        if word in _TRANSITION_KEEP_SCI and (sci or word not in _TRANSITION_REPLACE):
            return m.group(0)
        options = _TRANSITION_REPLACE.get(word, ("",))
        if sci:
            options = tuple(o for o in options if o not in ("So",)) or ("",)
        choice = options[rng.randrange(len(options))] if rng is not None else options[0]
        nxt = m.group("next")
        if not choice:
            return m.group("lead") + nxt.upper() if nxt.isalpha() else m.group("lead") + nxt
        if choice.endswith(","):
            return f"{m.group('lead')}{choice} {nxt}"
        return f"{m.group('lead')}{choice} {nxt.lower() if nxt.isalpha() and not _is_proper(m.string, m.start('next')) else nxt}"

    return _TRANSITION_DROP.sub(_sub, text)


def _is_proper(s: str, idx: int) -> bool:
    word = re.match(r"[A-Za-z]+", s[idx:])
    if not word:
        return True
    w = word.group(0)
    return w.isupper() or (w[0].isupper() and w.lower() not in _COMMON_STARTERS)


_COMMON_STARTERS = {
    "the", "this", "these", "that", "those", "it", "they", "we", "a", "an", "in", "on",
    "for", "as", "such", "many", "most", "some", "each", "our", "their", "its", "there",
    "when", "while", "although", "because", "if", "researchers", "studies", "results",
}


def split_tails(text: str) -> str:
    """Turn shallow participial tails into their own sentence (", highlighting X" -> ". This shows X")."""

    def _thereby(m: re.Match[str]) -> str:
        return f". This {ing_to_3sg(m.group('verb'))}"

    text = _THEREBY_RE.sub(_thereby, text)

    def _tail(m: re.Match[str]) -> str:
        # Only split when the clause before the comma can stand alone (subject + verb).
        before = re.split(r"[.!?:;]\s", m.string[: m.start()])[-1]
        if len(before.split()) < 6 or before.count("(") != before.count(")"):
            return m.group(0)
        verb = m.group("verb")
        head, _, rest = verb.partition(" ")
        main = _TAIL_VERBS.get(head, ing_to_3sg(head))
        return f". This {main}{(' ' + rest) if rest else ''}"

    return _TAIL_RE.sub(_tail, text)


def fix_parallelism(text: str) -> str:
    """'not only A but also B' -> 'A and B'; "It's not just A, it's B" -> "It's B"."""

    def _not_only(m: re.Match[str]) -> str:
        a = m.group("a").strip().rstrip(",")
        b = m.group("b").strip()
        return f"{a} and {b}"

    text = _NOT_ONLY_RE.sub(_not_only, text)

    def _not_just(m: re.Match[str]) -> str:
        return f"{m.group('subj')} {m.group('b').strip()}"

    return _NOT_JUST_RE.sub(_not_just, text)


def fix_dashes(text: str) -> str:
    """Replace em-dash asides with commas (keeps numeric en-dash ranges like 10–20)."""
    text = re.sub(r"[ \t]+--[ \t]+", " — ", text)
    # Paired asides: "X — aside — Y" -> "X, aside, Y"
    text = re.sub(r"\s*—\s*([^—\n.!?]{1,80}?)\s*—\s*", r", \1, ", text)
    text = re.sub(r"\s*—\s*", ", ", text)
    text = re.sub(r"(?<=[A-Za-z])[ \t]+–[ \t]+(?=[A-Za-z])", ", ", text)
    return text


def drop_chatbot_sentences(text: str) -> str:
    sents = split_sentences(text)
    if len(sents) <= 1:
        return text
    kept = [s for s in sents if not _CHATBOT_SENTENCE.match(s.strip())]
    if len(kept) == len(sents) or not kept:
        return text
    return " ".join(kept)


def vary_rhythm(text: str, cfg: Optional[HumanizerConfig] = None) -> str:
    """Split over-long compound sentences at safe clause boundaries for burstiness."""
    if cfg is not None and not cfg.use_burstiness:
        return text
    sents = split_sentences(text)
    if len(sents) == 0:
        return text
    min_long = cfg.min_long_words if cfg is not None else 24
    prob = cfg.burst_split_prob if cfg is not None else 1.0
    rng = cfg.rng if cfg is not None else None
    sci = cfg is None or cfg.scientific
    out: list[str] = []
    changed = False
    for s in sents:
        n = len(s.split())
        if n < max(min_long, 14) or (rng is not None and rng.random() > prob):
            out.append(s)
            continue
        best = None
        for m in re.finditer(r"(?:;\s+|,\s+(?P<conj>and|but|so|yet|while)\s+)", s):
            left = s[: m.start()]
            right = s[m.end():]
            lw, rw = len(left.split()), len(right.split())
            if lw < 8 or rw < 7:
                continue
            if m.group(0).startswith(",") and not _DETERMINER_START.match(right):
                continue
            if "__LOCK_" in right[:12] or left.count("(") != left.count(")"):
                continue
            score = abs(lw - rw)
            if best is None or score < best[0]:
                best = (score, m, left, right)
        if best is None:
            out.append(s)
            continue
        _, m, left, right = best
        conj = (m.group("conj") or "").lower()
        if conj == "but":
            lead = "But " if not sci else "However, "
        elif conj == "yet":
            lead = "Yet " if not sci else "Still, "
        elif conj == "so":
            lead = "So " if not sci else "As a result, "
        elif conj == "while":
            lead = "Meanwhile, "
        else:
            lead = ""
        right = right.strip()
        right_cased = right if (right[:1].isupper() and _is_proper(right, 0)) else right[:1].lower() + right[1:]
        new_right = (lead + right_cased) if lead else _cap_first(right)
        out.append(left.rstrip(" ,;") + ". " + new_right)
        changed = True
    return " ".join(out) if changed else text


# ---------------------------------------------------------------------------
# Grammar repair
# ---------------------------------------------------------------------------
_A_EXCEPT = re.compile(r"^(?:uni(?!n|mp|nt|mm)|us[eu]|uti|ura|ure|uri|uro|eu|one\b|once|ubiq|ufo|uk|uto|ute|ewe)", re.IGNORECASE)
_AN_EXCEPT = re.compile(r"^(?:hour|honest|honou?r|heir|herb\b)", re.IGNORECASE)


def _article_fix(m: re.Match[str]) -> str:
    art, space, word = m.group(1), m.group(2), m.group(3)
    if word.startswith("__") or any(c.isdigit() for c in word) or (word.isupper() and len(word) > 1):
        return m.group(0)
    starts_vowel = word[0].lower() in "aeiou"
    want_an = (starts_vowel and not _A_EXCEPT.match(word)) or bool(_AN_EXCEPT.match(word))
    if want_an and art.lower() == "a":
        new = "an"
    elif not want_an and art.lower() == "an":
        new = "a"
    else:
        return m.group(0)
    return match_case(art, new) + space + word


def fix_grammar(text: str) -> str:
    """Repair spacing, punctuation, articles, doubled words and sentence capitalisation."""
    if not text:
        return text
    text = re.sub(r"^[ \t]*[,;:][ \t]*", "", text, flags=re.MULTILINE)
    text = re.sub(r"([.!?])[ \t]*[,;:](?=[ \t])", r"\1", text)
    text = re.sub(r"[ \t]+([,.;:!?%)\]}])", r"\1", text)
    text = re.sub(r"([(\[{])[ \t]+", r"\1", text)
    text = re.sub(r",[ \t]*,+", ",", text)
    text = re.sub(r",(?=[A-Za-z])", ", ", text)
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)
    text = re.sub(r"[,;:]\.", ".", text)
    text = re.sub(r"\.,", ".", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\b(a|an|A|An)(\s+)([A-Za-z_][\w'-]*)", _article_fix, text)
    text = re.sub(r"\b(the|a|an|and|or|of|in|on|at|to|is|are|was|it|this|that)\s+\1\b", r"\1", text, flags=re.IGNORECASE)
    # Capitalise sentence starts (purely lower-case words only — leaves pH, mRNA, iOS alone).
    lines = [capitalize_sentences(line) if line.strip() else line for line in text.split("\n")]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------
def rewrite(text: str, cfg: Optional[HumanizerConfig] = None, *, rhythm: bool = True) -> str:
    """Full offline rewrite of a paragraph. Protected elements are never modified."""
    if not text or not text.strip():
        return text
    locked, vault = lock_elements(text)
    locked = drop_chatbot_sentences(locked)
    locked = apply_phrase_subs(locked, cfg)
    locked = fix_parallelism(locked)
    locked = split_tails(locked)
    locked = apply_word_subs(locked, cfg)
    locked = drop_transitions(locked, cfg)
    if cfg is None or cfg.use_structural:
        locked = fix_dashes(locked)
    if rhythm:
        locked = vary_rhythm(locked, cfg)
    locked = apply_contractions(locked, cfg)
    locked = fix_grammar(locked)
    out = unlock_elements(locked, vault)
    # Never return something that lost protected content.
    for orig in vault.values():
        if orig not in out:
            logger.debug("Rule rewrite dropped protected span %r; returning original", orig)
            return text
    return out


def polish(text: str, cfg: Optional[HumanizerConfig] = None) -> str:
    """Light deterministic clean-up for LLM output (no rhythm changes)."""
    return rewrite(text, cfg, rhythm=False)


def ensure_nltk_data() -> None:
    """Kept for backwards compatibility: the package no longer needs NLTK data."""
    return None


__all__ = [
    "lock_elements", "unlock_elements", "apply_phrase_subs", "apply_word_subs",
    "apply_contractions", "fix_grammar", "drop_transitions", "split_tails",
    "fix_parallelism", "fix_dashes", "vary_rhythm", "rewrite", "polish",
    "DEFAULT_SUBSTITUTIONS", "DEFAULT_CONTRACTIONS", "ensure_nltk_data",
]
