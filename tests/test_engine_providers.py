import io
import random
import threading
import urllib.error

import pytest

from humanizer import engine, providers
from humanizer.config import HumanizerConfig
from humanizer.engine import _segment, _strip_wrappers, check_fidelity, humanize_text
from tests.conftest import AI_TEXT

MD = (
    "# A heading here\n\n"
    "Furthermore, this approach leverages robust data to delve into results. It is important to note that the yield was 82%.\n\n"
    "- Moreover, the bullet item leverages things in many ways today.\n"
    "- second item\n\n"
    "```\ncode leverages stuff here and there ok\n```\n"
    "Last line."
)


# --------------------------------------------------------------------- engine


def test_segmentation_is_lossless():
    for t in (MD, "a\nb\n\nc", "\n\nlead", "trail\n\n", "x"):
        assert "".join(p + b + s for p, b, s, _ in _segment(t)) == t


def test_humanize_text_offline_keeps_markdown(cfg):
    out = humanize_text(MD, cfg)
    assert out.startswith("# A heading here\n\n")
    assert "- second item" in out and "```\ncode leverages stuff here and there ok\n```" in out
    assert "82%" in out and "leverages robust data" not in out
    assert cfg.engine_used == "rules"


def test_no_providers_falls_back_to_rules():
    c = HumanizerConfig(use_gemini=True, rng=random.Random(0))
    out = humanize_text(AI_TEXT, c)
    assert out != AI_TEXT and c.engine_used == "rules"


def test_strip_wrappers():
    raw = "<think>plan</think>Sure! Here is the rewritten text:\n\n\"The result holds.\"\n\nNote: I kept the facts."
    assert _strip_wrappers(raw) == "The result holds."


@pytest.mark.parametrize("cand,ok", [
    ("The yield reached 82% in trial [3], per (Smith, 2020), with <b id='1'>care</b> and __RUN_LOCKED_0__ kept intact.", True),
    ("The yield reached 85% in trial [3], per (Smith, 2020), with <b id='1'>care</b> and __RUN_LOCKED_0__ kept intact.", False),
    ("The yield reached 82% in trial, per (Smith, 2020), with <b id='1'>care</b> and __RUN_LOCKED_0__ kept intact here.", False),
    ("The yield reached 82% in trial [3], per (Smith, 2020), with care and __RUN_LOCKED_0__ kept intact here today.", False),
    ("The yield reached 82% in trial [3], per (Smith, 2020), with <b id='1'>care</b> and nothing else kept intact.", False),
])
def test_check_fidelity(cand, ok):
    src = "The yield was 82% in the trial [3] according to (Smith, 2020), measured with <b id='1'>care</b> and __RUN_LOCKED_0__ intact."
    assert check_fidelity(src, cand)[0] is ok


def test_llm_path_accepts_faithful_and_rejects_unfaithful(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    calls = []

    def fake_pool(prompt, cfg=None, temperature=0.9):
        calls.append(prompt)
        if len(calls) == 1:
            return "Sure, here it is: The yield was 99% and nothing else."  # number changed -> rejected
        return ("Doctors now use AI to diagnose, treat and manage patients. The yield was 82% in 2021. "
                "Costs fell. Machine learning reads huge amounts of medical data and finds patterns people miss.")

    monkeypatch.setattr(engine, "call_llm_pool", fake_pool)
    src = ("Artificial intelligence has emerged as a transformative force, revolutionizing how doctors diagnose, treat, "
           "and manage patients. The yield was 82% in 2021. Furthermore, costs were reduced significantly. Machine learning "
           "algorithms analyze vast amounts of medical data, identifying patterns that humans miss.")
    c = HumanizerConfig(use_gemini=True, stealth_level=2, retry_budget=2, rng=random.Random(0))
    out = humanize_text(src, c)
    assert "82%" in out and "99%" not in out
    assert c.engine_used == "llm"
    assert len(calls) == 2


def test_llm_failure_falls_back(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    def boom(*a, **k):
        raise RuntimeError("All LLM providers failed. Last error: boom")

    monkeypatch.setattr(engine, "call_llm_pool", boom)
    c = HumanizerConfig(use_gemini=True, rng=random.Random(0))
    out = humanize_text(AI_TEXT, c)
    assert out != AI_TEXT and c.engine_used == "rules"


def test_cancel_returns_original_text():
    ev = threading.Event()
    ev.set()
    c = HumanizerConfig(use_gemini=False, cancel_event=ev)
    assert humanize_text(AI_TEXT, c) == AI_TEXT


def test_progress_reported(cfg):
    seen = []
    cfg.progress_callback = lambda cur, tot, pct, msg: seen.append(pct)
    humanize_text(AI_TEXT + "\n\n" + AI_TEXT, cfg)
    assert seen and seen[-1] == 100.0


# --------------------------------------------------------------------- providers


def test_env_file_parsing(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# c\nexport GROQ_API_KEY="abc"\nMISTRAL_API_KEY=xyz  # note\nEMPTY=\n', encoding="utf-8")
    parsed = providers._parse_env_file(f)
    assert parsed == {"GROQ_API_KEY": "abc", "MISTRAL_API_KEY": "xyz"}


def test_pool_building_and_model_override(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODELS", "m1,m2")
    pool = providers._build_pool()
    assert [(e["name"], e["model"]) for e in pool] == [("Groq", "m1"), ("Groq", "m2")]
    assert all("api_key" not in s for s in providers.get_provider_status())


def test_ambient_github_token_is_not_used(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_ambient")
    assert providers._build_pool() == []
    providers._FILE_KEYS.add("GITHUB_TOKEN")
    assert providers._build_pool()[0]["name"] == "GitHub Models"


def test_failover_and_cooldown(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.setenv("CEREBRAS_API_KEY", "k2")
    monkeypatch.setenv("GROQ_MODELS", "g")
    monkeypatch.setenv("CEREBRAS_MODELS", "c")
    hits = []

    def fake_call(prompt, temperature, base, model, key, timeout=0, max_tokens=None):
        hits.append(model)
        if model == "g":
            raise urllib.error.HTTPError(base, 429, "rate", {"retry-after": "30"}, io.BytesIO(b"slow down"))
        return "ok"

    monkeypatch.setattr(providers, "_call_endpoint", fake_call)
    results = {providers.call_llm_pool("hi") for _ in range(3)}
    assert results == {"ok"}
    assert hits.count("g") == 1  # cooled after its 429, never retried
    status = {s["model"]: s for s in providers.get_provider_status()}
    assert status["g"]["status"] == "cooling_down" and status["g"]["last_error"] == "rate limited"


def test_daily_quota_and_auth_errors(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    err = urllib.error.HTTPError("u", 429, "q", {}, io.BytesIO(b"Tokens per day limit reached"))
    providers._handle_error(err, "https://api.groq.com/openai/v1", "m", "Groq")
    key = providers._ep_key("https://api.groq.com/openai/v1", "m")
    assert providers._COOLDOWNS[key] - providers.time.time() > 3600
    assert "daily" in providers._LAST_ERROR[key]


def test_no_providers_error_message():
    with pytest.raises(RuntimeError, match="No LLM providers"):
        providers.call_llm_pool("x")


def test_reserve_slot_spaces_calls_without_blocking(monkeypatch):
    monkeypatch.setattr(providers, "_MIN_INTERVAL", 2.0)
    a = providers._reserve_slot("https://a.example/v1")
    b = providers._reserve_slot("https://a.example/v1")
    other = providers._reserve_slot("https://b.example/v1")
    assert a == 0 and 1.9 < b <= 2.0 and other == 0
