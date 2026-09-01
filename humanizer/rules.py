"""
Humanizer Rule Pipeline Module.
Contains dictionary substitutions, WordNet synonym rankings, sentence burstiness
restructuring, human imperfection injections, and grammar repair utilities.
"""

from __future__ import annotations

import logging
import os
import random
import re
import zipfile
from functools import lru_cache
from typing import Optional

import nltk
from nltk.corpus import wordnet
from nltk.tokenize import sent_tokenize, word_tokenize

from humanizer.config import HumanizerConfig

logger = logging.getLogger(__name__)

_REQUIRED_NLTK = [
    ("corpora/wordnet", "wordnet"),
    ("corpora/omw-1.4", "omw-1.4"),
    ("tokenizers/punkt", "punkt"),
    ("tokenizers/punkt_tab", "punkt_tab"),
    ("taggers/averaged_perceptron_tagger", "averaged_perceptron_tagger"),
    ("taggers/averaged_perceptron_tagger_eng", "averaged_perceptron_tagger_eng"),
]


def ensure_nltk_data() -> None:
    """Ensure all required NLTK data packages are installed and extracted."""
    for resource_path, package in _REQUIRED_NLTK:
        try:
            nltk.data.find(resource_path)
        except LookupError:
            try:
                nltk.download(package, quiet=True)
            except Exception as e:
                logger.error(f"Failed to download NLTK package {package}: {e}")

            for nltk_path in nltk.data.path:
                zip_path = os.path.join(nltk_path, resource_path + ".zip")
                dest_dir = os.path.join(nltk_path, os.path.dirname(resource_path))
                target_dir = os.path.join(nltk_path, resource_path)
                if os.path.exists(zip_path) and not os.path.exists(target_dir):
                    try:
                        os.makedirs(dest_dir, exist_ok=True)
                        with zipfile.ZipFile(zip_path, "r") as zip_ref:
                            zip_ref.extractall(dest_dir)
                    except Exception as e:
                        logger.error(f"Failed to extract NLTK zip {zip_path}: {e}")


_PTB_TO_WN = {
    "JJ": wordnet.ADJ,
    "JJR": wordnet.ADJ,
    "JJS": wordnet.ADJ,
    "NN": wordnet.NOUN,
    "NNS": wordnet.NOUN,
    "NNP": wordnet.NOUN,
    "NNPS": wordnet.NOUN,
    "RB": wordnet.ADV,
    "RBR": wordnet.ADV,
    "RBS": wordnet.ADV,
    "VB": wordnet.VERB,
    "VBD": wordnet.VERB,
    "VBG": wordnet.VERB,
    "VBN": wordnet.VERB,
    "VBP": wordnet.VERB,
    "VBZ": wordnet.VERB,
}

_LOCK_RE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"__RUN_PLACEHOLDER_\d+__"), "PLACEHOLDER"),
    (re.compile(r"\[[a-zA-Z0-9,\-\s]{1,15}\]"), "CITATION"),
    (re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ]+"), "SUP_SUB"),
    (re.compile(r"\([A-Z][^()]{1,60}(?:19|20)\d{2}[^()]{0,30}\)"), "CITATION"),
    (re.compile(r"\[\d+(?:[,\-]\s*\d+)*\]"), "CITATION"),
    (re.compile(r"https?://\S+"), "URL"),
    (re.compile(r"www\.\S+"), "URL"),
    (re.compile(r"doi:\s*10\.\S+", re.IGNORECASE), "DOI"),
    (re.compile(r"\b(?:p|n|r|r²|R²|β|α|df|F|t|χ²|OR|RR|HR)\s*[=<>≤≥]\s*[\d.]+"), "STAT"),
    (re.compile(r"\b\d[\d,.]*\s*(?:%|mg|kg|ml|mL|μg|μL|mmol|cm|mm|nm|kb|Mb|GB|TB)\b"), "STAT"),
    (re.compile(r"\$[^$]+\$"), "EQUATION"),
    (re.compile(r"\\[a-zA-Z]+\{[^}]*\}"), "LATEX"),
]


def lock_elements(text: str) -> tuple[str, dict[str, str]]:
    vault: dict[str, str] = {}
    counter = [0]

    def make_token(label: str, span: str) -> str:
        tok = f"__LOCK_{label}_{counter[0]}__"
        counter[0] += 1
        vault[tok] = span
        return tok

    for pattern, label in _LOCK_RE:
        def replacer(m: re.Match[str], lbl: str = label) -> str:
            return make_token(lbl, m.group(0))
        text = pattern.sub(replacer, text)
    return text, vault


def unlock_elements(text: str, vault: dict[str, str]) -> str:
    for tok, orig in vault.items():
        text = text.replace(tok, orig)
    return text


_CONNECTIVES = {
    "furthermore": "also",
    "moreover": "also",
    "additionally": "also",
    "consequently": "so",
    "therefore": "so",
    "hence": "so",
    "thus": "so",
    "accordingly": "so",
    "subsequently": "next",
    "previously": "earlier",
    "nevertheless": "still",
    "nonetheless": "still",
    "notwithstanding": "still",
    "whereas": "while",
    "whilst": "while",
    "albeit": "though",
    "heretofore": "until now",
    "hitherto": "until now",
}

_FORMAL_VERBS = {
    "utilize": "use", "utilizes": "uses", "utilized": "used", "utilizing": "using",
    "utilise": "use", "utilises": "uses", "utilised": "used", "utilising": "using",
    "demonstrate": "show", "demonstrates": "shows", "demonstrated": "showed", "demonstrating": "showing",
    "indicate": "show", "indicates": "shows", "indicated": "showed", "indicating": "showing",
    "facilitate": "help", "facilitates": "helps", "facilitated": "helped", "facilitating": "helping",
    "implement": "set up", "implements": "sets up", "implemented": "set up", "implementing": "setting up",
    "commence": "start", "commences": "starts", "commenced": "started", "commencing": "starting",
    "terminate": "end", "terminates": "ends", "terminated": "ended", "terminating": "ending",
    "ascertain": "check", "ascertains": "checks", "ascertained": "checked",
    "endeavor": "try", "endeavors": "tries", "endeavored": "tried",
    "endeavour": "try", "endeavours": "tries", "endeavoured": "tried",
    "possess": "have", "possesses": "has", "possessed": "had", "possessing": "having",
    "acquire": "get", "acquires": "gets", "acquired": "got", "acquiring": "getting",
    "obtain": "get", "obtains": "gets", "obtained": "got", "obtaining": "getting",
    "purchase": "buy", "purchases": "buys", "purchased": "bought", "purchasing": "buying",
    "render": "make", "renders": "makes", "rendered": "made", "rendering": "making",
    "construct": "build", "constructs": "builds", "constructed": "built", "constructing": "building",
    "employ": "use", "employs": "uses", "employed": "used", "employing": "using",
    "require": "need", "requires": "needs", "required": "needed", "requiring": "needing",
    "attempt": "try", "attempts": "tries", "attempted": "tried", "attempting": "trying",
    "encompass": "cover", "encompasses": "covers", "encompassed": "covered", "encompassing": "covering",
    "exhibit": "show", "exhibits": "shows", "exhibited": "showed", "exhibiting": "showing",
    "yield": "produce", "yields": "produces", "yielded": "produced", "yielding": "producing",
    "modify": "change", "modifies": "changes", "modified": "changed", "modifying": "changing",
    "alter": "change", "alters": "changes", "altered": "changed", "altering": "changing",
    "permit": "allow", "permits": "allows", "permitted": "allowed", "permitting": "allowing",
    "investigate": "look at", "investigates": "looks at", "investigated": "looked at", "investigating": "looking at",
    "examine": "look at", "examines": "looks at", "examined": "looked at", "examining": "looking at",
    "evaluate": "judge", "evaluates": "judges", "evaluated": "judged", "evaluating": "judging",
    "assess": "check", "assesses": "checks", "assessed": "checked", "assessing": "checking",
    "anticipate": "expect", "anticipates": "expects", "anticipated": "expected", "anticipating": "expecting",
    "request": "ask for", "requests": "asks for", "requested": "asked for", "requesting": "asking for",
    "inquire": "ask", "inquires": "asks", "inquired": "asked", "inquiring": "asking",
    "respond": "reply", "responds": "replies", "responded": "replied", "responding": "replying",
    "participate": "join", "participates": "joins", "participated": "joined", "participating": "joining",
    "proceed": "go ahead", "proceeds": "goes ahead", "proceeded": "went ahead", "proceeding": "going ahead",
    "elucidate": "explain", "elucidates": "explains", "elucidated": "explained", "elucidating": "explaining",
    "delineate": "outline", "delineates": "outlines", "delineated": "outlined", "delineating": "outlining",
    "illustrate": "show", "illustrates": "shows", "illustrated": "showed", "illustrating": "showing",
    "denote": "mean", "denotes": "means", "denoted": "meant",
    "ensure": "make sure", "ensures": "makes sure", "ensured": "made sure", "ensuring": "making sure",
    "maintain": "keep", "maintains": "keeps", "maintained": "kept", "maintaining": "keeping",
    "retain": "keep", "retains": "keeps", "retained": "kept", "retaining": "keeping",
    "necessitate": "need", "necessitates": "needs", "necessitated": "needed", "necessitating": "needing",
    "assist": "help", "assists": "helps", "assisted": "helped", "assisting": "helping",
    "convey": "say", "conveys": "says", "conveyed": "said", "conveying": "saying",
    "comprehend": "get", "comprehends": "gets", "comprehended": "got", "comprehending": "getting",
    "depict": "show", "depicts": "shows", "depicted": "showed", "depicting": "showing",
}

_AI_TELL_VERBS = {
    "delve": "look at", "delves": "looks at", "delved": "looked at", "delving": "looking into",
    "leverage": "use", "leverages": "uses", "leveraged": "used", "leveraging": "using",
    "harness": "use", "harnesses": "uses", "harnessed": "used", "harnessing": "using",
    "unlock": "open up", "unlocks": "opens up", "unlocked": "opened up", "unlocking": "opening up",
    "embark": "start", "embarks": "starts", "embarked": "started", "embarking": "starting",
    "foster": "build", "fosters": "builds", "fostered": "built", "fostering": "building",
    "cultivate": "grow", "cultivates": "grows", "cultivated": "grew", "cultivating": "growing",
    "nurture": "grow", "nurtures": "grows", "nurtured": "grew", "nurturing": "growing",
    "elevate": "raise", "elevates": "raises", "elevated": "raised", "elevating": "raising",
    "showcase": "show", "showcases": "shows", "showcased": "showed", "showcasing": "showing",
    "underscore": "stress", "underscores": "stresses", "underscored": "stressed", "underscoring": "stressing",
    "highlight": "show", "highlights": "shows", "highlighted": "showed", "highlighting": "showing",
    "garner": "get", "garners": "gets", "garnered": "got", "garnering": "getting",
    "navigate": "handle", "navigates": "handles", "navigated": "handled", "navigating": "handling",
    "streamline": "simplify", "streamlines": "simplifies", "streamlined": "simplified", "streamlining": "simplifying",
    "optimize": "improve", "optimizes": "improves", "optimized": "improved", "optimizing": "improving",
    "optimise": "improve", "optimises": "improves", "optimised": "improved", "optimising": "improving",
    "revolutionize": "change", "revolutionizes": "changes", "revolutionized": "changed", "revolutionizing": "changing",
    "transform": "change", "transforms": "changes", "transformed": "changed", "transforming": "changing",
    "illuminate": "explain", "illuminates": "explains", "illuminated": "explained", "illuminating": "explaining",
    "empower": "help", "empowers": "helps", "empowered": "helped", "empowering": "helping",
    "spearhead": "lead", "spearheads": "leads", "spearheaded": "led", "spearheading": "leading",
    "champion": "back", "champions": "backs", "championed": "backed", "championing": "backing",
    "propel": "push", "propels": "pushes", "propelled": "pushed", "propelling": "pushing",
    "catalyze": "trigger", "catalyzes": "triggers", "catalyzed": "triggered", "catalyzing": "triggering",
    "catalyse": "trigger", "catalyses": "triggers", "catalysed": "triggered", "catalysing": "triggering",
}

_FORMAL_ADJECTIVES = {
    "comprehensive": "full", "extensive": "wide", "substantial": "large", "significant": "notable",
    "considerable": "large", "sufficient": "enough", "adequate": "enough", "numerous": "many",
    "various": "many", "multiple": "many", "manifold": "many", "myriad": "many",
    "copious": "plenty of", "abundant": "plenty of", "ample": "enough", "additional": "more",
    "supplementary": "extra",
}

_AI_TELL_ADJECTIVES = {
    "transformative": "major", "pivotal": "key", "paramount": "top", "crucial": "key",
    "vital": "key", "imperative": "must-have", "indispensable": "needed", "cornerstone": "basis",
    "linchpin": "key part", "groundbreaking": "new", "unprecedented": "unmatched",
    "monumental": "huge", "revolutionary": "radical", "innovative": "fresh", "novel": "new",
    "cutting-edge": "advanced", "state-of-the-art": "latest", "bespoke": "tailored",
    "holistic": "all-round", "multifaceted": "complex", "nuanced": "subtle", "intricate": "complex",
    "seamless": "smooth", "robust": "strong", "resilient": "tough", "dynamic": "active",
    "vibrant": "lively", "burgeoning": "growing", "unwavering": "steady", "meticulous": "careful",
    "exhaustive": "thorough", "rigorous": "strict", "profound": "deep", "astounding": "amazing",
    "breathtaking": "stunning", "stellar": "great", "optimal": "best", "efficacious": "working",
    "ubiquitous": "everywhere", "pervasive": "common", "quintessential": "classic",
    "archetypal": "typical", "exemplary": "model", "unmatched": "unequalled",
    "invaluable": "priceless", "indubitable": "certain", "unequivocal": "clear",
}

_FORMAL_NOUNS = {
    "methodology": "method", "methodologies": "methods", "utilization": "use", "utilisation": "use",
    "modification": "change", "modifications": "changes", "alteration": "change", "alterations": "changes",
    "commencement": "start", "termination": "end", "acquisition": "purchase", "acquisitions": "purchases",
    "endeavor": "effort", "endeavors": "efforts", "endeavour": "effort", "endeavours": "efforts",
    "objective": "goal", "objectives": "goals", "requirement": "need", "requirements": "needs",
    "assistance": "help", "inquiry": "question", "inquiries": "questions", "investigation": "study",
    "investigations": "studies", "evaluation": "review", "evaluations": "reviews", "assessment": "review",
    "assessments": "reviews", "implication": "effect", "implications": "effects", "perspective": "view",
    "perspectives": "views", "dimension": "aspect", "dimensions": "aspects", "facet": "side",
    "facets": "sides", "phenomenon": "event", "phenomena": "events",
}

_AI_TELL_NOUNS = {
    "tapestry": "mix", "testament": "proof", "landscape": "field", "realm": "area",
    "paradigm": "model", "paradigms": "models", "synergy": "teamwork", "synergies": "gains",
    "nexus": "link", "panoply": "range", "plethora": "abundance", "myriad": "many",
    "spectrum": "range", "conduit": "channel", "catalyst": "spark", "catalysts": "sparks",
    "beacon": "guide", "bedrock": "base", "touchstone": "test", "bastion": "stronghold",
}

_FORMAL_ADVERBS = {
    "substantially": "greatly", "significantly": "notably", "considerably": "much",
    "adequately": "well enough", "sufficiently": "enough", "primarily": "mainly",
    "principally": "mainly", "predominantly": "mostly", "exclusively": "only",
    "subsequently": "later", "previously": "before", "consequently": "so",
    "accordingly": "so", "alternatively": "instead", "comparatively": "by comparison",
    "relatively": "fairly", "approximately": "about", "precisely": "exactly",
    "invariably": "always", "continually": "constantly", "momentarily": "briefly",
}

_AI_TELL_ADVERBS = {
    "seamlessly": "smoothly", "meticulously": "carefully", "profoundly": "deeply",
    "fundamentally": "at heart", "inherently": "by nature", "intrinsically": "by nature",
    "exponentially": "rapidly", "dramatically": "sharply", "markedly": "clearly",
    "strikingly": "noticeably", "unquestionably": "without doubt", "undeniably": "clearly",
    "indisputably": "certainly", "inextricably": "closely", "holistically": "as a whole",
    "dynamically": "actively", "uniquely": "rarely", "pivotal": "key",
}

DEFAULT_SUBSTITUTIONS: dict[str, str] = {
    **_CONNECTIVES,
    **_FORMAL_VERBS,
    **_AI_TELL_VERBS,
    **_FORMAL_ADJECTIVES,
    **_AI_TELL_ADJECTIVES,
    **_FORMAL_NOUNS,
    **_AI_TELL_NOUNS,
    **_FORMAL_ADVERBS,
    **_AI_TELL_ADVERBS,
}

_PHRASE_PAIRS: list[tuple[str, str]] = [
    (r"\ba rich tapestry of\b", "a broad mix of"),
    (r"\ba tapestry of\b", "a mix of"),
    (r"\ba testament to\b", "clear evidence of"),
    (r"\bstands as a testament to\b", "shows"),
    (r"\bserves as a testament to\b", "shows"),
    (r"\bin the realm of\b", "in"),
    (r"\bin the landscape of\b", "across"),
    (r"\bacross the landscape of\b", "across"),
    (r"\bthe broader landscape of\b", ""),
    (r"\bplays a crucial role in\b", "is central to"),
    (r"\bplays a pivotal role in\b", "is key to"),
    (r"\bplays a vital role in\b", "matters for"),
    (r"\bplays a key role in\b", "helps drive"),
    (r"\bplay a crucial role in\b", "are central to"),
    (r"\bplay a pivotal role in\b", "are key to"),
    (r"\bit is important to note that\b", ""),
    (r"\bit is worth noting that\b", ""),
    (r"\bit is worth mentioning that\b", ""),
    (r"\bit should be noted that\b", ""),
    (r"\bit is essential to understand that\b", ""),
    (r"\bit goes without saying that\b", "clearly,"),
    (r"\bfirst and foremost\b", "first,"),
    (r"\blast but not least\b", "finally,"),
    (r"\bin light of the fact that\b", "because"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bfor the purpose of\b", "to"),
    (r"\bwith a view to\b", "to"),
    (r"\bin order to\b", "to"),
    (r"\bin the event that\b", "if"),
    (r"\bin the near future\b", "soon"),
    (r"\bat the present time\b", "now"),
    (r"\bat this point in time\b", "now"),
    (r"\bhas the potential to\b", "can"),
    (r"\bhave the potential to\b", "can"),
    (r"\bpaves the way for\b", "leads to"),
    (r"\bpaving the way for\b", "leading to"),
    (r"\bopens up new avenues for\b", "creates opportunities for"),
    (r"\ba wide range of\b", "many"),
    (r"\ba wide variety of\b", "many"),
    (r"\ba broad spectrum of\b", "many"),
    (r"\ba multitude of\b", "many"),
    (r"\ba plethora of\b", "plenty of"),
    (r"\ba myriad of\b", "many"),
    (r"\bgame[- ]changer\b", "major shift"),
    (r"\bgame[- ]changing\b", "major"),
    (r"\bparadigm shift\b", "major change"),
    (r"\bdouble-edged sword\b", "mixed blessing"),
    (r"\bthe tip of the iceberg\b", "only the beginning"),
    (r"\bdelving deep into\b", "looking closely at"),
    (r"\bdelving into\b", "looking at"),
    (r"\bdelve into\b", "look at"),
    (r"\bunleash the power of\b", "use"),
    (r"\bunleashing the power of\b", "using"),
    (r"\bharnessing the power of\b", "using"),
    (r"\bharness the power of\b", "use"),
    (r"\bnavigating the complexities of\b", "handling"),
    (r"\bnavigate the complexities of\b", "handle"),
]

DEFAULT_CONTRACTIONS: dict[str, str] = {
    "are not": "aren't", "cannot": "can't", "could not": "couldn't", "did not": "didn't",
    "does not": "doesn't", "do not": "don't", "had not": "hadn't", "has not": "hasn't",
    "have not": "haven't", "he is": "he's", "he will": "he'll", "he would": "he'd",
    "I am": "I'm", "I have": "I've", "I will": "I'll", "I would": "I'd",
    "is not": "isn't", "it is": "it's", "it will": "it'll", "must not": "mustn't",
    "she is": "she's", "she will": "she'll", "she would": "she'd", "should not": "shouldn't",
    "that is": "that's", "there is": "there's", "they are": "they're", "they have": "they've",
    "they will": "they'll", "they would": "they'd", "we are": "we're", "we have": "we've",
    "we will": "we'll", "we would": "we'd", "were not": "weren't", "what is": "what's",
    "who is": "who's", "will not": "won't", "would not": "wouldn't", "you are": "you're",
    "you have": "you've", "you will": "you'll", "you would": "you'd",
}


def _smartcase(match: re.Match, replacement: str) -> str:
    tok = match.group(0)
    if tok.isupper():
        return replacement.upper()
    if tok[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def apply_phrase_subs(text: str, cfg: HumanizerConfig) -> str:
    if not cfg.use_phrase_subs:
        return text
    for pat_str, repl in _PHRASE_PAIRS:
        if cfg.rng.random() > cfg.phrase_prob:
            continue
        rx = re.compile(pat_str, re.IGNORECASE)
        text = rx.sub(lambda m, r=repl: _smartcase(m, r), text)
    return text


def apply_word_subs(text: str, cfg: HumanizerConfig) -> str:
    subs = getattr(cfg, "substitutions", None) or DEFAULT_SUBSTITUTIONS
    for word, repl in subs.items():
        if cfg.rng.random() > cfg.subs_prob:
            continue
        rx = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
        text = rx.sub(lambda m, r=repl: _smartcase(m, r), text)
    return text


def apply_contractions(text: str, cfg: HumanizerConfig) -> str:
    if not cfg.use_contractions or cfg.scientific:
        return text
    contractions = getattr(cfg, "contractions", None) or DEFAULT_CONTRACTIONS
    for full, short in contractions.items():
        if cfg.rng.random() > cfg.contract_prob:
            continue
        rx = re.compile(r"\b" + re.escape(full) + r"\b", re.IGNORECASE)
        text = rx.sub(lambda m, s=short: _smartcase(m, s), text)
    return text


def fix_grammar(text: str) -> str:
    """Repair common punctuation, whitespace, and grammatical slips."""
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([(\[{])\s+", r"\1", text)
    text = re.sub(r"\s+([)\]}])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\b(a)\s+([aeiouAEIOU]\w+)", r"an \2", text)
    text = re.sub(r"\b(an)\s+([^aeiouAEIOU\s\W]\w+)", r"a \2", text)
    text = re.sub(r"\b(the|a|an|and|or|in|on|at|to)\s+\1\b", r"\1", text, flags=re.IGNORECASE)
    return text
