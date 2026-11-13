import random

import pytest

from humanizer.config import HumanizerConfig
from humanizer.patterns import find_tells
from humanizer.rules import (
    apply_contractions,
    apply_word_subs,
    fix_dashes,
    fix_grammar,
    fix_parallelism,
    lock_elements,
    rewrite,
    split_tails,
    unlock_elements,
)
from humanizer.text import capitalize_sentences, conjugate, ing_to_3sg, split_sentences

# --------------------------------------------------------------------- text


def test_sentence_split_keeps_abbreviations_and_decimals():
    s = "Smith et al. reported a 3.5% gain (Fig. 2). The yield was 82%. Dr. J. Smith agreed! Was it e.g. robust? Yes."
    assert split_sentences(s) == [
        "Smith et al. reported a 3.5% gain (Fig. 2).",
        "The yield was 82%.",
        "Dr. J. Smith agreed!",
        "Was it e.g. robust?",
        "Yes.",
    ]


@pytest.mark.parametrize("ing,expected", [
    ("improving", "improves"), ("reducing", "reduces"), ("creating", "creates"), ("amplifying", "amplifies"),
    ("underlying", "underlies"), ("stopping", "stops"), ("fostering", "fosters"), ("pushing", "pushes"),
    ("Enabling", "Enables"), ("yielding", "yields"), ("using", "uses"), ("having", "has"),
])
def test_ing_to_3sg(ing, expected):
    assert ing_to_3sg(ing) == expected


def test_conjugate_regular_irregular_and_phrasal():
    assert conjugate("look into")["ing"] == "looking into"
    assert conjugate("stop")["past"] == "stopped"
    assert conjugate("simplify")["3sg"] == "simplifies"
    assert conjugate("show")["pp"] == "shown"
    assert conjugate("develop")["ing"] == "developing"


def test_capitalize_sentences_respects_abbreviations():
    out = capitalize_sentences("results. the yield rose. as Smith et al. the model works, e.g. the catalyst.")
    assert out == "Results. The yield rose. As Smith et al. the model works, e.g. the catalyst."


# --------------------------------------------------------------------- locking


def test_lock_roundtrip_and_protection():
    t = r"See [1, 2] and (Smith et al., 2020); p < 0.05 at 25 °C with 3.2 mA cm-2, $E=mc^2$, https://x.org/a and doi: 10.1000/xyz123."
    locked, vault = lock_elements(t)
    for protected in ("[1, 2]", "(Smith et al., 2020)", "p < 0.05", "25 °C", "$E=mc^2$", "https://x.org/a"):
        assert protected not in locked
    assert unlock_elements(locked, vault) == t


def test_latex_nested_roundtrip():
    t = r"\textbf{see [1]}"
    locked, vault = lock_elements(t)
    assert unlock_elements(locked, vault) == t


# --------------------------------------------------------------------- rewriting


def test_academic_register_keeps_domain_terms(cfg):
    t = ("The catalyst lowered the activation barrier and the absorption spectrum shifted. "
         "The yield was statistically significant and the robust estimator held. This novel method is dynamic.")
    out = rewrite(t, cfg)
    for term in ("catalyst", "spectrum", "yield", "significant", "robust", "novel", "dynamic"):
        assert term in out


def test_ai_vocabulary_removed_with_correct_inflection(gcfg):
    out = apply_word_subs("They are leveraging data. It leverages tools. It has demonstrated gains. They demonstrated it.", gcfg)
    assert "using data" in out and "uses tools" in out
    assert "has shown gains" in out and "They showed it" in out


def test_full_rewrite_removes_tells_and_keeps_facts(cfg):
    t = ("Furthermore, it is important to note that the model leverages multifaceted data to delve into outcomes, "
         "thereby enabling researchers to streamline workflows. The yield was 82% (p < 0.05) as shown by Smith et al. (2021) [3]. "
         "This approach not only improves accuracy but also reduces costs, highlighting the pivotal role of AI. "
         "The method serves as a robust indicator — a testament to its power — and plays a crucial role in the field. I hope this helps!")
    out = rewrite(t, cfg)
    for keep in ("82%", "p < 0.05", "Smith et al. (2021)", "[3]"):
        assert keep in out
    for gone in ("Furthermore", "it is important to note", "leverages", "delve", "thereby", "not only",
                 "pivotal", "—", "testament", "plays a crucial role", "I hope this helps", "serves as"):
        assert gone.lower() not in out.lower(), gone
    assert out[0].isupper()


def test_rewrite_is_stable_on_human_text(cfg):
    from tests.conftest import HUMAN_TEXT

    out = rewrite(HUMAN_TEXT, cfg)
    assert "0.5" not in out or "0.5" in HUMAN_TEXT
    assert "pH" in out and "Priya" in out
    # Re-running must not keep changing clean text.
    assert rewrite(out, cfg) == rewrite(rewrite(out, cfg), cfg)


def test_parallelism_tails_dashes():
    assert fix_parallelism("AI not only improves accuracy but also reduces costs.") == "AI improves accuracy and reduces costs."
    assert split_tails("The model trains fast, thereby reducing cost.") == "The model trains fast. This reduces cost."
    assert fix_dashes("The tool — fast and cheap — works.") == "The tool, fast and cheap, works."
    assert fix_dashes("Ranges 10–20 stay.") == "Ranges 10–20 stay."


def test_contractions_only_in_general_register():
    sci = HumanizerConfig(scientific=True, rng=random.Random(1))
    gen = HumanizerConfig(scientific=False, contract_prob=1.0, rng=random.Random(1))
    t = "It is clear that we are ready and they do not wait."
    assert apply_contractions(t, sci) == t
    out = apply_contractions(t, gen)
    assert "we're" in out and "don't" in out


@pytest.mark.parametrize("bad,good", [
    ("a apple", "an apple"), ("an banana", "a banana"), ("an university", "a university"),
    ("a hour", "an hour"), ("an European", "a European"), ("a FTIR scan", "a FTIR scan"),
])
def test_article_fix(bad, good):
    assert fix_grammar(bad) == good[0].upper() + good[1:]


def test_grammar_preserves_newlines_and_ellipsis():
    assert fix_grammar("Line one ,ok\nline two...") == "Line one, ok\nLine two..."


def test_find_tells_spans_are_exact():
    text = "We delve into a rich tapestry of results. Moreover, it is pivotal."
    for t in find_tells(text):
        assert text[t["start"]:t["end"]] == t["text"]
    cats = {t["category"] for t in find_tells(text)}
    assert {"vocabulary", "transition"} <= cats
