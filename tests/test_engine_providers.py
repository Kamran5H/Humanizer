import io
import json
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


def test_llm_cleanup_does_not_rewrite_the_model_output_with_offline_rules(cfg):
    text = "Our robust mechanism remains stable, resulting in a higher yield under the tested conditions."
    assert engine._clean_rewrite(text, text, cfg) == text


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


@pytest.mark.parametrize("src,cand,reason", [
    ("The treatment did not reduce mortality.", "The treatment did reduce mortality.", "negation changed"),
    ("We observed no benefit.", "We observed benefit.", "negation changed"),
    ("We didn't observe benefit.", "We did observe benefit.", "negation changed"),
    ("The yield was 82%.", "The yield was 82% in 2025.", "number changed"),
    ("Both trials had 82% yield and 82% purity.", "Both trials had 82% yield and purity.", "number changed"),
    ("The treatment may improve outcomes.", "The treatment improves outcomes.", "uncertainty changed"),
])
def test_fidelity_rejects_negation_and_numeric_changes_even_for_short_text(src, cand, reason):
    assert check_fidelity(src, cand, short=True) == (False, reason)


def test_fidelity_accepts_equivalent_negative_contraction():
    assert check_fidelity("The treatment did not help.", "The treatment didn't help.", short=True)[0]
    assert check_fidelity("The treatment not only helped but also reduced costs.",
                          "The treatment helped and reduced costs.", short=True)[0]


def test_offline_rewrite_also_checks_negation(monkeypatch, cfg):
    src = "The treatment did not reduce mortality in the clinical trial."
    monkeypatch.setattr(engine, "rewrite", lambda text, config: text.replace(" not", ""))
    assert humanize_text(src, cfg) == src
    assert cfg.stats["kept"] == 1


@pytest.mark.parametrize("candidate", [
    "A <b id='1'>bold claim stays here.",
    "A <b id='1'>bold claim</b></b> stays here.",
    "A <b id='1'>bold claim</b><b id='1'>extra claim</b> stays here.",
    "A <b id='1'>bold <i id='2'>claim</i></b> stays here.",
])
def test_fidelity_rejects_malformed_or_invented_formatting(candidate):
    assert not check_fidelity("A <b id='1'>bold claim</b> stays here.", candidate, short=True)[0]


def test_strict_ai_mode_requires_an_api_key():
    with pytest.raises(ValueError, match="No AI provider configured"):
        humanize_text(AI_TEXT, HumanizerConfig(allow_offline_fallback=False))


def test_strict_ai_mode_does_not_substitute_offline_output(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fail(*args, **kwargs):
        raise RuntimeError("All LLM providers failed")

    monkeypatch.setattr(engine, "call_llm_pool", fail)
    config = HumanizerConfig(allow_offline_fallback=False)
    with pytest.raises(ValueError, match="No safe AI rewrite"):
        humanize_text(AI_TEXT, config)
    assert not config.stats.get("rules")


def test_anthropic_messages_transport(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODELS", "chosen-claude-model")
    seen = []

    def respond(request, timeout):
        seen.append(request)
        return io.BytesIO(json.dumps({"content": [{"type": "thinking", "thinking": "private"},
                                                  {"type": "text", "text": "Rewritten text."}],
                                     "stop_reason": "end_turn"}).encode())

    monkeypatch.setattr(providers.urllib.request, "urlopen", respond)
    assert providers.call_llm_pool("Edit this text", temperature=1.2) == "Rewritten text."
    request = seen[0]
    assert request.full_url == "https://api.anthropic.com/v1/messages"
    assert request.get_header("X-api-key") == "test-key"
    assert request.get_header("Anthropic-version") == "2023-06-01"
    payload = json.loads(request.data)
    assert payload["model"] == "chosen-claude-model"
    assert "temperature" not in payload  # unsupported by newer Claude models
    assert payload["system"] and payload["messages"] == [{"role": "user", "content": "Edit this text"}]


@pytest.mark.parametrize("result", [
    {"content": [{"type": "text", "text": "Incomplete"}], "stop_reason": "max_tokens"},
    {"content": [{"type": "thinking", "thinking": "No answer"}], "stop_reason": "end_turn"},
    {"content": [{"type": "text", "text": "Refused."}], "stop_reason": "refusal"},
    {"content": [{"type": "text", "text": "Still working."}], "stop_reason": "pause_turn"},
])
def test_anthropic_rejects_truncated_or_blank_output(monkeypatch, result):
    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps(result).encode()))
    with pytest.raises(ValueError):
        providers._call_endpoint("prompt", 0.9, "https://api.anthropic.com/v1", "claude", "test")


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
    assert providers._build_pool() == []
    assert "retired" in providers.configuration_warnings()[0]
    monkeypatch.setenv("GITHUB_MODELS_TOKEN", "legacy-models-key")
    assert providers._build_pool() == []


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


def test_gateway_configuration_and_rotation(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "omni-test")
    monkeypatch.setenv("OMNIROUTE_MODELS", "combo-editor,claude-route")
    monkeypatch.setenv("FREELLMAPI_API_KEY", "free-test")
    monkeypatch.setenv("FREELLMAPI_MODELS", "auto:reliable")
    pool = providers._build_pool()
    assert [(p["name"], p["model"]) for p in pool] == [
        ("OmniRoute", "combo-editor"), ("OmniRoute", "claude-route"), ("FreeLLMAPI", "auto:reliable"),
    ]
    assert pool[0]["base_url"] == "http://127.0.0.1:20128/v1"
    assert pool[2]["base_url"] == "http://127.0.0.1:3001/v1"
    calls = []
    monkeypatch.setattr(providers, "_call_endpoint", lambda prompt, temp, base, model, key, **kwargs: calls.append(model) or "OK")
    for _ in range(3):
        assert providers.call_llm_pool("test") == "OK"
    assert set(calls) == {"combo-editor", "claude-route", "auto:reliable"}
    assert all(p["last_success_at"] for p in providers.get_provider_status())


def test_anonymous_free_provider_is_opt_in_and_paced_per_model(monkeypatch):
    assert not providers._build_pool()
    monkeypatch.setenv("HUMANIZER_ENABLE_OVH_ANONYMOUS", "1")
    monkeypatch.setenv("OVH_MODELS", "model-a,model-b")
    assert len(providers._build_pool()) == 2
    base = "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1"
    assert providers._reserve_slot(base, "model-a") == 0
    assert providers._reserve_slot(base, "model-b") == 0
    assert 30.0 < providers._reserve_slot(base, "model-a") <= 30.1


def test_anonymous_requests_have_no_fake_authorization_header(monkeypatch):
    def respond(request, **kwargs):
        assert request.get_header("Authorization") is None
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}).encode())

    monkeypatch.setattr(providers.urllib.request, "urlopen", respond)
    assert providers._call_endpoint("test", 0, "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1", "model", "anonymous") == "OK"


def test_cancelled_request_does_not_mark_provider_as_broken(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setenv("GROQ_MODELS", "model")
    event = threading.Event()

    def cancel(*args, **kwargs):
        event.set()
        raise RuntimeError("Cancelled")

    monkeypatch.setattr(providers, "_call_endpoint", cancel)
    with pytest.raises(RuntimeError, match="Cancelled"):
        providers.call_llm_pool("test", HumanizerConfig(cancel_event=event))
    assert not providers._COOLDOWNS


def test_omniroute_default_and_empty_model_handling(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test")
    monkeypatch.delenv("OMNIROUTE_MODELS", raising=False)
    pool = providers._build_pool()
    assert [(p["name"], p["model"]) for p in pool] == [
        ("OmniRoute", "auto/best-free"),
        ("OmniRoute", "auto/best-chat"),
    ]
    assert not providers.configuration_warnings()
    monkeypatch.setenv("OMNIROUTE_MODELS", "   ")
    assert "OMNIROUTE_MODELS" in providers.configuration_warnings()[0]


def test_connection_probe_records_real_success_and_auth_failure(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "not-a-real-key")
    monkeypatch.setenv("GROQ_MODELS", "good,bad")

    def endpoint(prompt, temp, base, model, key, **kwargs):
        assert "OK" in prompt and kwargs["max_tokens"] == 128
        if model == "bad":
            raise urllib.error.HTTPError(base, 401, "bad key", {}, io.BytesIO(b"Unauthorized"))
        return "OK"

    monkeypatch.setattr(providers, "_call_endpoint", endpoint)
    statuses = {p["model"]: p for p in providers.check_connections()}
    assert statuses["good"]["last_success_at"]
    assert statuses["bad"]["status"] == "cooling_down"
    assert "auth failed" in statuses["bad"]["last_error"]
    assert all("api_key" not in entry for entry in statuses.values())
