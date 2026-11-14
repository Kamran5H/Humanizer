import json
from pathlib import Path

from humanizer.detector import analyze, score_text, smooth_sentences
from humanizer.lint import is_clean, lint_text, readability
from humanizer.rules import rewrite
from tests.conftest import AI_TEXT, HUMAN_TEXT

CORPUS = json.loads((Path(__file__).resolve().parent.parent / "tools" / "corpus.json").read_text(encoding="utf-8"))


def test_ai_corpus_scores_high():
    for item in CORPUS:
        assert score_text(item["text"])["ai_score"] >= 70, item["id"]
    assert score_text(AI_TEXT)["ai_score"] >= 70


def test_human_text_scores_low():
    assert score_text(HUMAN_TEXT)["ai_score"] < 30


def test_rewrite_lowers_score(cfg):
    for item in CORPUS:
        before = score_text(item["text"])["ai_score"]
        after = score_text(rewrite(item["text"], cfg))["ai_score"]
        assert after < before - 15, item["id"]


def test_short_text_is_not_scored():
    r = score_text("Too short.")
    assert r["ai_score"] == 0.0 and r["note"]


def test_score_identifies_its_limits():
    for text in ("Too short.", AI_TEXT, HUMAN_TEXT):
        result = score_text(text)
        assert result["score_type"] == "writing_pattern_index"
        assert result["calibrated"] is False
        assert "not a probability" in result["score_explanation"]
        assert "Turnitin" in result["score_explanation"]
        assert "human-written" not in result["verdict"]


def test_analyze_offsets_and_signals():
    a = analyze(AI_TEXT)
    assert {s["key"] for s in a["signals"]} >= {"burstiness", "vocabulary", "structure", "uniformity"}
    for t in a["tells"]:
        assert AI_TEXT[t["start"]:t["end"]] == t["text"]
    for s in a["sentence_scores"]:
        assert 0 <= s["start"] < s["end"] <= len(AI_TEXT)
    assert a["perplexity"] is None or a["perplexity"] > 0


def test_smooth_sentences_sorted_worst_first():
    sm = smooth_sentences(AI_TEXT, threshold=0)
    scores = [s for _, _, s in sm]
    assert scores == sorted(scores, reverse=True)


def test_lint_detects_fidelity_problems():
    src = "The yield was 82% [3] (Smith, 2020)."
    out = "The yield was 85% [4]."
    msgs = " ".join(m for _, m in lint_text(out, src))
    assert "fabricated citation" in msgs and "dropped source citation" in msgs and "number" in msgs
    assert not is_clean(out, src)
    assert is_clean(src, src)


def test_lint_flags_placeholders_and_articles():
    msgs = " ".join(m for _, m in lint_text("A __LOCK_QTY_0__ left in a apple."))
    assert "placeholder" in msgs and "a/an" in msgs


def test_readability_values():
    r = readability(HUMAN_TEXT)
    assert r["flesch"] is not None and r["avg_sentence_len"] > 0
    assert readability("")["flesch"] is None
