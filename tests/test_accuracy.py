"""Adversarial factual edits and independent human prose, not score chasing."""
import pytest

from humanizer import engine
from humanizer.config import HumanizerConfig
from humanizer.detector import analyze
from humanizer.engine import check_fidelity, humanize_paragraph_rules, humanize_paragraph_stealth
from humanizer.lint import lint_text
from humanizer.rules import rewrite


@pytest.mark.parametrize('source,candidate', [
    ('The dose was 5 mg daily.', 'The dose was 5 kg daily.'),
    ('The value was -5 degrees.', 'The value was 5 degrees.'),
    ('The value was +5 degrees.', 'The value was -5 degrees.'),
    ('The temperature was 25 °C.', 'The temperature was 25 °F.'),
    ('The result had p < 0.05.', 'The result had p > 0.05.'),
    ('The result had p < 0.05.', 'The result had p = 0.05.'),
    ('The solution was 5 μM.', 'The solution was 5 mM.'),
    ('The treatment decreased mortality.', 'The treatment increased mortality.'),
    ('The catalyst lowered the barrier.', 'The catalyst raised the barrier.'),
    ('The treatment might improve survival.', 'The treatment likely improves survival.'),
    ('See https://example.org/a for the protocol.', 'See https://other.org/a for the protocol.'),
    ('The dose was 5 mg [1].', 'The dose was 5 mg [2].'),
    ('We used the method (Smith & Jones, 2020).', 'We used the method (Smith & Jones, 2021).'),
    ('The value was 1e-5.', 'The value was 1e5.'),
    ('The equation is $E=mc^2$.', 'The equation is $E=mc^3$.'),
    ('She called it "statistically significant".', 'She called it "significant".'),
    ('The catalyst lowered the barrier.', 'The dog chased a ball.'),
])
def test_reject_factual_drift_even_in_short_rewrites(source, candidate):
    assert not check_fidelity(source, candidate, short=True)[0]
    assert any(severity == 'error' for severity, _ in lint_text(candidate, source))


def test_accept_equivalent_protected_facts():
    assert check_fidelity('We used 5 mg daily [1] (Smith & Jones, 2020).',
                          'Daily, we used 5 mg [1] (Smith & Jones, 2020).')[0]
    assert check_fidelity('The treatment decreased mortality.', 'The treatment reduced mortality.', short=True)[0]


@pytest.mark.parametrize('source', [
    'The old testament describes an ancient realm and a tapestry.',
    'She said "leverage is crucial" in the interview.',
    'The pressure fell, thereby reducing the output under these conditions.',
])
def test_offline_preserves_literal_and_quoted_claims(source, cfg):
    assert humanize_paragraph_rules(source, cfg) == source


def test_parallelism_keeps_both_topics(cfg):
    out = rewrite('This is not just about cost, it is about safety.', cfg)
    assert 'cost' in out and 'safety' in out


def test_refinement_rechecked_against_original(monkeypatch):
    source = 'The treatment reduced mortality by 5% in the trial conducted at the hospital during the study period.'
    monkeypatch.setattr(engine, 'call_llm_pool', lambda *a, **kw: source)
    monkeypatch.setattr(engine, '_roughen_smooth_sentences', lambda *a: source.replace('5%', '50%'))
    config = HumanizerConfig(stealth_level=3, retry_budget=1, allow_offline_fallback=False)
    assert humanize_paragraph_stealth(source, config) == source


def test_cleanup_does_not_hide_invented_evidence(cfg):
    source = 'The method improves measurement precision in the laboratory.'
    candidate = source + ' [99] https://invented.example'
    cleaned = engine._clean_rewrite(source, candidate, cfg)
    assert '[99]' in cleaned
    assert not check_fidelity(source, cleaned)[0]


def test_insufficient_text_has_explicit_unavailable_score():
    result = analyze('A short ordinary sentence about the actual study results.')
    assert result['score_available'] is False and result['confidence'] == 'low'
    assert result['signals'] == []


def test_non_latin_text_is_not_mistaken_for_low_pattern_english():
    result = analyze('یہ ایک جملہ ہے۔ ' * 50)
    assert not result['score_available']
    assert 'unsupported' in result['note']


def test_single_sentence_does_not_invent_rhythm_evidence():
    text = ('We measured the temperature in each room and recorded the readings in the notebook '
            'before moving the equipment to the other building where the team repeated the measurements '
            'using a second instrument from the laboratory and compared both sets of readings')
    result = analyze(text)
    assert result['score_available']
    signals = {s['key']: s for s in result['signals']}
    assert signals['burstiness']['available'] is False
    assert signals['uniformity']['available'] is False
    assert signals['burstiness']['ai'] is None


def test_highlight_offsets_include_leading_whitespace():
    text = '\n   Furthermore, ' + 'the instrument recorded measurements in the laboratory. ' * 8
    result = analyze(text)
    for span in result['tells']:
        assert text[span['start']:span['end']] == span['text']
    for span in result['sentence_scores']:
        assert text[span['start']:span['end']].strip()
        assert text[span['start']] != ' '
