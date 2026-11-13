"""
AI-writing pattern catalogue — the single source of truth.

Every other module (detector, rule rewriter, linter, LLM prompt builder, web UI
highlighter) reads from this file, so a pattern added here is detected, flagged
and avoided everywhere at once.

The catalogue merges the project's own measured tells with the categories in
Wikipedia's "Signs of AI writing" guide and the open-source ``blader/humanizer``
skill: inflated significance, promotional language, copula avoidance, shallow
-ing tails, negative parallelism, stacked hedges, chatbot residue, knowledge
cut-off disclaimers, formulaic transitions and em-dash overuse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Tell:
    category: str
    pattern: str
    suggestion: str
    weight: float = 1.0


# Human-readable labels for the UI.
CATEGORY_LABELS: dict[str, str] = {
    "vocabulary": "Overused AI vocabulary",
    "significance": "Inflated significance",
    "promotional": "Promotional language",
    "copula": "Avoids plain 'is/has'",
    "filler": "Filler phrase",
    "hedging": "Stacked hedging",
    "transition": "Formulaic transition",
    "tail": "Shallow -ing tail",
    "parallelism": "Negative parallelism",
    "chatbot": "Chatbot residue",
    "cutoff": "Knowledge-cutoff disclaimer",
    "dash": "Em-dash overuse",
    "conclusion": "Generic upbeat conclusion",
}

_V = "vocabulary"

TELLS: tuple[Tell, ...] = (
    # ---- Overused AI vocabulary ------------------------------------------------
    Tell(_V, r"\bdelv(?:e|es|ed|ing)\b", "look into / examine"),
    Tell(_V, r"\bleverag(?:e|es|ed|ing)\b", "use"),
    Tell(_V, r"\butili[sz](?:e|es|ed|ing|ation)\b", "use"),
    Tell(_V, r"\bharness(?:es|ed|ing)?\b(?=\s+(?:the|its|their|this|these|our|AI|data|power))", "use"),
    Tell(_V, r"\bfoster(?:s|ed|ing)?\b", "encourage / build"),
    Tell(_V, r"\bunderscor(?:e|es|ed|ing)\b", "stress / show"),
    Tell(_V, r"\bshowcas(?:e|es|ed|ing)\b", "show"),
    Tell(_V, r"\bbolster(?:s|ed|ing)?\b", "strengthen"),
    Tell(_V, r"\bgarner(?:s|ed|ing)?\b", "gain / get"),
    Tell(_V, r"\bembark(?:s|ed|ing)? on\b", "start"),
    Tell(_V, r"\bstreamlin(?:e|es|ed|ing)\b", "simplify"),
    Tell(_V, r"\bempower(?:s|ed|ing|ment)?\b", "enable / let"),
    Tell(_V, r"\bspearhead(?:s|ed|ing)?\b", "lead"),
    Tell(_V, r"\brevolutioni[sz](?:e|es|ed|ing)\b", "change"),
    Tell(_V, r"\btransformative\b", "major"),
    Tell(_V, r"\bpivotal\b", "central / important"),
    Tell(_V, r"\bcrucial(?:ly)?\b", "important", 0.6),
    Tell(_V, r"\bparamount\b", "most important"),
    Tell(_V, r"\bseamless(?:ly)?\b", "smooth"),
    Tell(_V, r"\bmultifaceted\b", "complex"),
    Tell(_V, r"\bholistic(?:ally)?\b", "overall / broad"),
    Tell(_V, r"\bmeticulous(?:ly)?\b", "careful"),
    Tell(_V, r"\bintricac(?:y|ies)\b", "details"),
    Tell(_V, r"\bintricate\b", "complex / detailed", 0.6),
    Tell(_V, r"\btapestry\b", "mix"),
    Tell(_V, r"\btestament\b", "proof / sign"),
    Tell(_V, r"\brealm\b", "field / area"),
    Tell(_V, r"\binterplay\b", "interaction"),
    Tell(_V, r"\bsynerg(?:y|ies|istic)\b", "cooperation"),
    Tell(_V, r"\bplethora\b", "many / plenty"),
    Tell(_V, r"\bmyriad\b", "many"),
    Tell(_V, r"\bcornerstone\b", "basis"),
    Tell(_V, r"\bbedrock\b", "basis"),
    Tell(_V, r"\blinchpin\b", "central part"),
    Tell(_V, r"\bbeacon\b", "model / example"),
    Tell(_V, r"\bburgeoning\b", "growing"),
    Tell(_V, r"\bunwavering\b", "steady"),
    Tell(_V, r"\bever-(?:evolving|changing|growing)\b", "changing / growing"),
    Tell(_V, r"\bgame[- ]?chang(?:er|ers|ing)\b", "major shift"),
    Tell(_V, r"\bcutting-edge\b", "advanced / latest"),
    Tell(_V, r"\bgroundbreaking\b", "new"),
    Tell(_V, r"\bunprecedented\b", "unusual / first", 0.7),
    Tell(_V, r"\bnoteworthy\b", "notable"),
    Tell(_V, r"\bcommendable\b", "good"),
    Tell(_V, r"\binvaluable\b", "very useful"),
    Tell(_V, r"\bindispensable\b", "essential"),
    Tell(_V, r"\benduring\b", "lasting", 0.6),
    Tell(_V, r"\bdeep dive\b", "close look"),
    Tell(_V, r"\balign(?:s|ed|ing)? with\b", "match / fit", 0.5),
    Tell(_V, r"\bnavigat(?:e|es|ed|ing) the (?:complexities|challenges|intricacies|landscape)\b", "handle"),
    # ---- Inflated significance ---------------------------------------------------
    Tell("significance", r"\bplays? (?:a |an )?(?:crucial|pivotal|vital|key|significant|central|important|critical) role\b", "matters / is central"),
    Tell("significance", r"\bstands? as a testament\b|\ba testament to\b", "shows / is evidence of"),
    Tell("significance", r"\b(?:setting|sets|set) the stage for\b", "leads to / prepares"),
    Tell("significance", r"\bpav(?:es|ed|ing|e) the way for\b", "leads to / allows"),
    Tell("significance", r"\bmark(?:s|ed|ing)? a (?:pivotal|significant|major|key) (?:moment|milestone|shift|turning point)\b", "state what changed"),
    Tell("significance", r"\b(?:in|across) the (?:ever-evolving |rapidly evolving |broader )?landscape of\b", "in"),
    Tell("significance", r"\bin the realm of\b", "in"),
    Tell("significance", r"\ba (?:rich|vibrant|diverse) tapestry\b", "a mix"),
    Tell("significance", r"\bindelible (?:mark|impact)\b", "lasting effect"),
    Tell("significance", r"\bfundamentally (?:transform|chang|reshap)\w*\b", "change"),
    Tell("significance", r"\bunleash(?:es|ed|ing)? the (?:full )?(?:power|potential)\b", "use"),
    Tell("significance", r"\bopens? up new (?:avenues|possibilities|horizons)\b", "allows"),
    # ---- Promotional ------------------------------------------------------------
    Tell("promotional", r"\bnestled\b", "located / in"),
    Tell("promotional", r"\bbreathtaking\b", "striking"),
    Tell("promotional", r"\bmust-(?:see|visit|have)\b", "notable"),
    Tell("promotional", r"\bin the heart of\b", "in"),
    Tell("promotional", r"\bvibrant\b", "lively", 0.7),
    Tell("promotional", r"\bbustling\b", "busy"),
    Tell("promotional", r"\bboasts?\b", "has"),
    Tell("promotional", r"\bseamlessly integrat\w+\b", "works with"),
    Tell("promotional", r"\bstate-of-the-art\b", "latest", 0.5),
    # ---- Copula avoidance ---------------------------------------------------------
    Tell("copula", r"\bserv(?:es|ed|ing|e) as (?:a|an|the)\b", "is"),
    Tell("copula", r"\bstands as (?:a|an|the)\b", "is"),
    # ---- Filler -------------------------------------------------------------------
    Tell("filler", r"\bit is (?:important|essential|crucial|worth) (?:to note|noting|mentioning|to remember|to recognize|to understand)\b", "delete"),
    Tell("filler", r"\bit should be noted that\b", "delete"),
    Tell("filler", r"\bit goes without saying\b", "delete"),
    Tell("filler", r"\bin today'?s (?:fast-paced|digital|modern|rapidly changing|ever-changing) (?:world|age|era|landscape)\b", "today"),
    Tell("filler", r"\bin the modern era\b", "today"),
    Tell("filler", r"\bas (?:we|society|the world|humanity) (?:navigate|move through|enter|embrace)\b", "delete the run-up"),
    Tell("filler", r"\bwhen it comes to\b", "for / in"),
    Tell("filler", r"\bat the end of the day\b", "in the end"),
    Tell("filler", r"\ba (?:wide|broad|diverse) (?:range|array|variety|spectrum) of\b", "many"),
    Tell("filler", r"\ba (?:multitude|plethora|myriad|host) of\b", "many"),
    Tell("filler", r"\bdue to the fact that\b|\bin light of the fact that\b", "because"),
    Tell("filler", r"\bfirst and foremost\b", "first"),
    Tell("filler", r"\blast but not least\b", "finally"),
    Tell("filler", r"\bat this point in time\b", "now"),
    Tell("filler", r"\bwith a view to\b", "to"),
    Tell("filler", r"\b(?:let'?s|let us) (?:dive|delve) in(?:to)?\b", "delete"),
    Tell("filler", r"\bhere'?s what you need to know\b", "delete"),
    Tell("filler", r"\bthe real question is\b|\bat its core\b|\bthe heart of the matter\b", "state the claim"),
    # ---- Hedging --------------------------------------------------------------------
    Tell("hedging", r"\b(?:could|may|might|can) potentially\b", "could / may"),
    Tell("hedging", r"\bit could be argued that\b", "delete or name who argues"),
    Tell("hedging", r"\bto some extent,? (?:it|this) (?:may|might|could)\b", "one qualifier"),
    # ---- Transitions (sentence-initial) ---------------------------------------------
    Tell("transition", r"(?:^|(?<=[.!?]\s))(?:Furthermore|Moreover|Additionally|In addition|Notably|Importantly|Consequently|Ultimately|Indeed|Interestingly|Crucially|Undeniably|Overall|In conclusion|In summary|To summarize|In essence),", "drop or use 'Also'/'So'"),
    # ---- Shallow -ing tails -------------------------------------------------------
    Tell("tail", r",\s(?:thereby|thus)\s\w+ing\b", "split: 'This ...'"),
    Tell("tail", r",\s(?:highlighting|underscoring|showcasing|emphasizing|emphasising|reflecting|symbolizing|symbolising|contributing to|fostering|ensuring|paving|reinforcing|solidifying|cementing)\b", "split into its own sentence"),
    # ---- Negative parallelism -----------------------------------------------------
    Tell("parallelism", r"\bnot only\b[^.!?]{1,120}?\bbut also\b", "say both plainly"),
    Tell("parallelism", r"\b(?:it'?s|it is|this is) not (?:just|merely|simply|only) (?:about )?[^.!?,;]{1,60}[,;] (?:it'?s|it is|but)\b", "state the point directly"),
    Tell("parallelism", r"\bno [a-z]+, no [a-z]+, just\b", "state the point directly"),
    # ---- Chatbot residue ------------------------------------------------------------
    Tell("chatbot", r"\bI hope this (?:helps|is helpful|clarifies)\b", "delete"),
    Tell("chatbot", r"\b(?:let me know|feel free to (?:ask|reach out))\b", "delete"),
    Tell("chatbot", r"\b(?:great|excellent|good) question\b", "delete"),
    Tell("chatbot", r"\bcertainly!|\babsolutely!|\bof course!", "delete"),
    Tell("chatbot", r"\bwould you like (?:me )?to\b", "delete"),
    Tell("chatbot", r"\bas an AI(?: language model)?\b", "delete"),
    # ---- Knowledge cut-off --------------------------------------------------------
    Tell("cutoff", r"\bas of my (?:last|latest) (?:knowledge |training )?(?:update|cutoff)\b", "delete"),
    Tell("cutoff", r"\bup to my (?:last )?(?:training|knowledge) (?:update|cutoff|data)\b", "delete"),
    Tell("cutoff", r"\b(?:is|are) not (?:publicly )?(?:available|documented) in (?:my|the) (?:training|available) data\b", "delete"),
    # ---- Generic conclusions --------------------------------------------------------
    Tell("conclusion", r"\bthe future (?:looks|is) (?:bright|promising)\b", "end on a concrete fact"),
    Tell("conclusion", r"\b(?:holds|hold) (?:great|immense|tremendous|enormous|significant) (?:promise|potential)\b", "end on a concrete fact"),
    Tell("conclusion", r"\bonly time will tell\b", "delete"),
    Tell("conclusion", r"\bremains to be seen\b", "delete", 0.6),
    Tell("conclusion", r"\bexciting (?:times|possibilities|future)\b", "delete"),
    # ---- Dashes -------------------------------------------------------------------------
    Tell("dash", r"\s?—\s?|\s--\s", "comma, colon or period", 0.5),
)

_FLAGS = re.IGNORECASE | re.MULTILINE


@lru_cache(maxsize=1)
def compiled_tells() -> list[tuple[Tell, re.Pattern[str]]]:
    out = []
    for t in TELLS:
        # Transition pattern is case-sensitive by design (sentence starts).
        flags = re.MULTILINE if t.category == "transition" else _FLAGS
        out.append((t, re.compile(t.pattern, flags)))
    return out


def find_tells(text: str) -> list[dict]:
    """Return non-overlapping tell spans: [{start, end, text, category, label, suggestion, weight}]."""
    if not text:
        return []
    hits: list[dict] = []
    for tell, rx in compiled_tells():
        for m in rx.finditer(text):
            if m.end() <= m.start():
                continue
            hits.append({
                "start": m.start(),
                "end": m.end(),
                "text": m.group(0),
                "category": tell.category,
                "label": CATEGORY_LABELS.get(tell.category, tell.category),
                "suggestion": tell.suggestion,
                "weight": tell.weight,
            })
    # Keep the longest span when two patterns overlap.
    hits.sort(key=lambda h: (h["start"], -(h["end"] - h["start"])))
    merged: list[dict] = []
    for h in hits:
        if merged and h["start"] < merged[-1]["end"]:
            continue
        merged.append(h)
    return merged


def tell_weight(text: str) -> float:
    return sum(h["weight"] for h in find_tells(text))


# Words to forbid in LLM prompts (kept short — long lists waste tokens and can
# prime the model toward the very words it should avoid).
BANNED_FOR_PROMPT: tuple[str, ...] = (
    "delve", "leverage", "utilize", "harness", "foster", "underscore", "showcase",
    "bolster", "garner", "streamline", "empower", "spearhead", "revolutionize",
    "transformative", "pivotal", "crucial", "paramount", "seamless", "multifaceted",
    "holistic", "meticulous", "intricate", "tapestry", "testament", "realm",
    "interplay", "synergy", "plethora", "myriad", "cornerstone", "beacon",
    "burgeoning", "ever-evolving", "game-changer", "cutting-edge", "groundbreaking",
    "noteworthy", "invaluable", "vibrant", "nestled", "boasts", "moreover",
    "furthermore", "additionally", "notably", "ultimately", "indeed",
)

BANNED_PHRASES_FOR_PROMPT: tuple[str, ...] = (
    "it is important to note", "it is worth noting", "plays a crucial/pivotal/key role",
    "serves as", "stands as a testament", "a testament to", "in today's world",
    "in the realm of", "the landscape of", "paves the way", "sets the stage",
    "not only ... but also", "it's not just X, it's Y", "when it comes to",
    "a wide range of", "holds great promise", "the future looks bright",
    "in conclusion", "I hope this helps", "let's dive in",
)
