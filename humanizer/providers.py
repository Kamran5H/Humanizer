"""
Multi-provider LLM pool with automatic failover.

* Discovers every OpenAI-compatible provider whose key is present in the
  environment, ``.env`` or ``.keys.env`` (Gemini, Groq, Cerebras, SambaNova,
  OpenRouter, Together, Mistral, DeepInfra, Anthropic, plus any custom
  ``STEALTH_API_BASE`` and a local Ollama fallback).
* Rotates across ready endpoints, respects per-host pacing without blocking
  other hosts, and puts failing endpoints on a cooldown that survives restarts.
* Thread-safe: worker threads share one pool.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from humanizer.config import HumanizerConfig

logger = logging.getLogger(__name__)

_STATE_DIR = Path(os.environ.get("HUMANIZER_HOME") or os.environ.get("LOCALAPPDATA")
                  or os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "humanizer_pro"
_STATE_FILE = _STATE_DIR / "provider_state.json"

_MIN_INTERVAL = float(os.environ.get("HUMANIZER_MIN_INTERVAL", "1.5"))  # seconds between calls to one host

_STATE_LOCK = threading.RLock()
_NEXT_SLOT_BY_HOST: dict[str, float] = {}
_COOLDOWNS: dict[str, float] = {}
_LAST_ERROR: dict[str, str] = {}
_LAST_SUCCESS: dict[str, float] = {}
_KEYS_LOADED = False
_CALL_COUNTER = 0
_FILE_KEYS: set[str] = set()  # names defined explicitly in a .env / .keys.env file

# Backwards-compatible aliases for older scripts/tests.
_OAI_COOLDOWNS = _COOLDOWNS

# (env var, base url, default models csv, display name)
_FREE_PROVIDER_REGISTRY: list[tuple[str, str, str, str]] = [
    ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite", "Gemini"),
    ("GROQ_API_KEY", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b,qwen/qwen3.8-27b", "Groq"),
    ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1", "gpt-oss-120b", "Cerebras"),
    ("SAMBANOVA_API_KEY", "https://api.sambanova.ai/v1", "Meta-Llama-3.3-70B-Instruct", "SambaNova"),
    ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", "openrouter/free", "OpenRouter"),
    ("TOGETHER_API_KEY", "https://api.together.xyz/v1", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "Together"),
    ("MISTRAL_API_KEY", "https://api.mistral.ai/v1", "mistral-small-latest", "Mistral"),
    ("DEEPINFRA_API_KEY", "https://api.deepinfra.com/v1/openai", "meta-llama/Llama-3.3-70B-Instruct", "DeepInfra"),
    ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini", "OpenAI"),
    ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1", "claude-sonnet-4-6", "Anthropic (Claude)"),
]

_SYSTEM_PROMPT = (
    "You are an expert human editor. You rewrite text so it reads as if a skilled person wrote it, "
    "while keeping every fact exactly. You reply with the rewritten text only."
)


# ---------------------------------------------------------------------------
# Key loading & persistent state
# ---------------------------------------------------------------------------
def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            k, v = line.split("=", 1)
            v = v.strip()
            if " #" in v and not v.startswith(("'", '"')):
                v = v.split(" #", 1)[0].strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            if k.strip() and v:
                out[k.strip()] = v
    except Exception as e:  # pragma: no cover - unreadable file
        logger.debug("Could not read %s: %s", path, e)
    return out


def key_file_candidates() -> list[Path]:
    root = Path(__file__).resolve().parent.parent
    home_cfg = Path.home() / ".config" / "humanizer"
    seen: list[Path] = []
    for base in (Path.cwd(), root, root.parent, home_cfg):
        for name in (".keys.env", ".env"):
            p = base / name
            if p not in seen:
                seen.append(p)
    return seen


def load_keys_if_needed(force: bool = False) -> None:
    global _KEYS_LOADED
    if _KEYS_LOADED and not force:
        return
    for p in key_file_candidates():
        if p.is_file():
            for k, v in _parse_env_file(p).items():
                os.environ.setdefault(k, v)
                _FILE_KEYS.add(k)
    _load_persistent_cooldowns()
    _KEYS_LOADED = True


def _load_persistent_cooldowns() -> None:
    if not _STATE_FILE.exists():
        return
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        now = time.time()
        with _STATE_LOCK:
            for ep, ts in (data.get("cooldowns") or {}).items():
                if isinstance(ts, (int, float)) and ts > now:
                    _COOLDOWNS[ep] = float(ts)
    except Exception as e:
        logger.debug("Failed to load cooldown state: %s", e)


def _save_persistent_cooldowns() -> None:
    try:
        with _STATE_LOCK:
            payload = {"cooldowns": {k: v for k, v in _COOLDOWNS.items() if v > time.time()},
                       "updated_at": time.time()}
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(_STATE_FILE)
    except Exception as e:
        logger.debug("Failed to save cooldown state: %s", e)


def reset_cooldowns() -> None:
    """Clear all persistent and in-memory cooldowns."""
    with _STATE_LOCK:
        _COOLDOWNS.clear()
        _LAST_ERROR.clear()
        _NEXT_SLOT_BY_HOST.clear()
    _save_persistent_cooldowns()


# ---------------------------------------------------------------------------
# Pool construction
# ---------------------------------------------------------------------------
def _host_key(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc or url
    except Exception:
        return url


def _ep_key(base_url: str, model: str) -> str:
    return f"{base_url}||{model}"


def _is_local(base_url: str) -> bool:
    host = _host_key(base_url).split(":")[0]
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.endswith(".local")


def _build_pool(primary_base: Optional[str] = None) -> list[dict]:
    load_keys_if_needed()
    pool: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, base: str, model: str, key: str) -> None:
        k = (base.rstrip("/"), model)
        if not base or not model or not key or k in seen:
            return
        seen.add(k)
        pool.append({"name": name, "base_url": base.rstrip("/"), "model": model, "api_key": key})

    base = (primary_base or os.environ.get("STEALTH_API_BASE", "")).strip()
    if base:
        key = os.environ.get("STEALTH_API_KEY", "").strip() or "none"
        models_raw = os.environ.get("STEALTH_MODEL", "").strip() or "openai/gpt-oss-120b"
        for m in [x.strip() for x in models_raw.split(",") if x.strip()]:
            add("Custom", base, m, key)

    # Gateways already manage upstream credentials/rotation. Keep their keys on
    # the server and use the model or combo IDs from the user's gateway catalog.
    for prefix, name, default_base, default_model in (
        ("OMNIROUTE", "OmniRoute", "http://127.0.0.1:20128/v1", "auto/best-free,auto/best-chat"),
        ("FREELLMAPI", "FreeLLMAPI", "http://127.0.0.1:3001/v1", "auto"),
    ):
        gateway_key = os.environ.get(f"{prefix}_API_KEY", "").strip()
        gateway_base = os.environ.get(f"{prefix}_BASE_URL", default_base).strip()
        raw_models = os.environ.get(f"{prefix}_MODELS")
        gateway_models = (default_model if raw_models is None else raw_models).strip()
        if gateway_key and gateway_models:
            for model in gateway_models.split(","):
                add(name, gateway_base, model.strip(), gateway_key)

    # Documented anonymous tier; opt in explicitly rather than sending a user's
    # files to a new public provider merely because no other key was found.
    if os.environ.get("HUMANIZER_ENABLE_OVH_ANONYMOUS", "").lower() in ("1", "true", "yes"):
        for model in os.environ.get("OVH_MODELS", "Meta-Llama-3_3-70B-Instruct,Mistral-Small-3.2-24B-Instruct-2506").split(","):
            add("OVHcloud (anonymous)", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1", model.strip(), "anonymous")

    for env_k, base_url, models_csv, display in _FREE_PROVIDER_REGISTRY:
        key = os.environ.get(env_k, "").strip()
        if not key:
            continue
        override_var = env_k.rsplit("_", 2)[0] + "_MODELS" if env_k.endswith("_API_KEY") else env_k.split("_")[0] + "_MODELS"
        override = os.environ.get(override_var, "").strip()
        for m in [x.strip() for x in (override or models_csv).split(",") if x.strip()]:
            add(display, base_url, m, key)

    fb = os.environ.get("STEALTH_FALLBACK_BASE", "").strip()
    if fb:
        fb_model = os.environ.get("STEALTH_FALLBACK_MODEL", "qwen2.5:7b").strip()
        fb_key = os.environ.get("STEALTH_FALLBACK_KEY", "ollama").strip() or "ollama"
        add("Local (Ollama)", fb, fb_model, fb_key)
    return pool


def has_providers() -> bool:
    """True when at least one LLM endpoint is configured."""
    return bool(_build_pool())


def configuration_warnings() -> list[str]:
    load_keys_if_needed()
    warnings = []
    omni_models = os.environ.get("OMNIROUTE_MODELS")
    if os.environ.get("OMNIROUTE_API_KEY", "").strip() and omni_models is not None and not omni_models.strip():
        warnings.append("OMNIROUTE_API_KEY is set, but OMNIROUTE_MODELS is empty. Copy a model or combo ID from the gateway dashboard (or omit OMNIROUTE_MODELS to use auto/best-free,auto/best-chat).")
    if os.environ.get("GITHUB_MODELS_TOKEN") or ("GITHUB_TOKEN" in _FILE_KEYS and os.environ.get("GITHUB_TOKEN")):
        warnings.append("GitHub Models retired on July 30, 2026. Its token is ignored; configure a supported provider or gateway.")
    return warnings


def available_count() -> int:
    now = time.time()
    with _STATE_LOCK:
        return sum(1 for e in _build_pool() if _COOLDOWNS.get(_ep_key(e["base_url"], e["model"]), 0.0) <= now)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _pacing_key(base_url: str, model: Optional[str] = None) -> str:
    host = _host_key(base_url)
    return f"{host}/{model}" if "endpoints.kepler.ai.cloud.ovh.net" in host else host


def _reserve_slot(base_url: str, model: Optional[str] = None) -> float:
    """Reserve the next call slot for a host and return how long to wait (lock is not held while sleeping)."""
    host = _pacing_key(base_url, model)
    interval = max(_MIN_INTERVAL, 30.1) if "endpoints.kepler.ai.cloud.ovh.net" in host else _MIN_INTERVAL
    with _STATE_LOCK:
        now = time.time()
        slot = max(now, _NEXT_SLOT_BY_HOST.get(host, 0.0))
        _NEXT_SLOT_BY_HOST[host] = slot + interval
        return slot - now


def _provider_wait(base_url: str, model: Optional[str] = None) -> float:
    with _STATE_LOCK:
        return max(0.0, _NEXT_SLOT_BY_HOST.get(_pacing_key(base_url, model), 0.0) - time.time())


def _wait_for_slot(base_url: str, model: str, cfg: Optional[HumanizerConfig] = None) -> None:
    """Reserve only when ready, so cancelled workers leave no future reservations."""
    announced = False
    while True:
        if cfg is not None and cfg.cancelled:
            raise RuntimeError("Cancelled")
        with _STATE_LOCK:
            delay = _provider_wait(base_url, model)
            if delay <= 0:
                _reserve_slot(base_url, model)
                return
        if cfg is not None and not announced:
            cfg.status(f"Waiting {delay:.0f}s for {model} rate pacing…")
            announced = True
        wait = min(delay, 30.0)
        if cfg is not None and cfg.cancel_event is not None:
            if cfg.cancel_event.wait(wait):
                raise RuntimeError("Cancelled")
        else:
            time.sleep(wait)


def _call_endpoint(prompt: str, temperature: float, base_url: str, model: str, api_key: str,
                   timeout: float = 90.0, max_tokens: Optional[int] = None) -> str:
    if urllib.parse.urlparse(base_url).hostname == "api.anthropic.com":
        return _call_anthropic(prompt, temperature, base_url, model, api_key, timeout, max_tokens)
    url = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
    if max_tokens is None:
        max_tokens = int(max(600, min(4096, len(prompt) / 2.2)))
    payload: dict = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
    }
    if "openrouter.ai" in base_url:
        payload["include_reasoning"] = False
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "HumanizerPro/6.0",
            "HTTP-Referer": "https://github.com/Kamran5H/Humanizer",
            "X-Title": "Humanizer Pro",
        },
    )
    if api_key != "anonymous":
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))

    choices = d.get("choices") or []
    if not choices:
        raise ValueError(f"Empty response from {base_url} ({model})")
    choice = choices[0]
    content = (choice.get("message") or {}).get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    text = (content or "").strip()
    if not text:
        raise ValueError(f"Blank content from {base_url} ({model})")
    if choice.get("finish_reason") == "length":
        raise ValueError(f"Truncated output from {model} (finish_reason=length)")
    return text


def _call_anthropic(prompt: str, temperature: float, base_url: str, model: str,
                    api_key: str, timeout: float, max_tokens: Optional[int]) -> str:
    """Claude uses the Messages API rather than OpenAI chat completions."""
    payload = {
        "model": model,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens or int(max(600, min(4096, len(prompt) / 2.2))),
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/messages",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01", "User-Agent": "HumanizerPro/6.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("stop_reason") == "max_tokens":
        raise ValueError(f"Truncated output from {model} (stop_reason=max_tokens)")
    if result.get("stop_reason") not in ("end_turn", "stop_sequence"):
        raise ValueError(f"Unfinished or refused response from {model} (stop_reason={result.get('stop_reason')})")
    text = "".join(block.get("text", "") for block in result.get("content", [])
                   if block.get("type") == "text").strip()
    if not text:
        raise ValueError(f"Blank content from Anthropic ({model})")
    return text


def _cool_endpoint(base_url: str, model: str, seconds: float, reason: str = "") -> None:
    key = _ep_key(base_url, model)
    with _STATE_LOCK:
        _COOLDOWNS[key] = max(_COOLDOWNS.get(key, 0.0), time.time() + seconds)
        if reason:
            _LAST_ERROR[key] = reason
    _save_persistent_cooldowns()


def _handle_error(e: BaseException, base_url: str, model: str, provider: str) -> None:
    with _STATE_LOCK:
        _LAST_SUCCESS.pop(_ep_key(base_url, model), None)
    if isinstance(e, urllib.error.HTTPError):
        code = e.code
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:600].lower()
        except Exception:
            pass
        if code == 429:
            daily = any(t in body for t in ("per day", "daily", "tpd", "tokens per day", "quota exceeded", "resource_exhausted"))
            if daily:
                _cool_endpoint(base_url, model, 6 * 3600, "daily quota exhausted")
                logger.warning("%s/%s daily quota exhausted; cooling 6h.", provider, model)
                return
            wait = 30.0
            ra = e.headers.get("retry-after") if e.headers else None
            if ra:
                try:
                    wait = max(3.0, min(600.0, float(ra)))
                except ValueError:
                    pass
            _cool_endpoint(base_url, model, wait, "rate limited")
            return
        if code in (401, 403):
            _cool_endpoint(base_url, model, 6 * 3600, f"auth failed ({code}) — check the API key")
            return
        if code == 404:
            _cool_endpoint(base_url, model, 12 * 3600, "model not found")
            return
        if code in (400, 413, 422):
            _cool_endpoint(base_url, model, 300, f"request rejected ({code})")
            return
        _cool_endpoint(base_url, model, 60, f"server error ({code})")
        return
    if isinstance(e, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)):
        _cool_endpoint(base_url, model, 45, "network error")
        return
    _cool_endpoint(base_url, model, 20, str(e)[:120])


def get_provider_status() -> list[dict]:
    """Live status of every configured endpoint (keys are never returned)."""
    pool = _build_pool()
    now = time.time()
    out = []
    with _STATE_LOCK:
        for ep in pool:
            key = _ep_key(ep["base_url"], ep["model"])
            cd = _COOLDOWNS.get(key, 0.0)
            wait = max(0.0, _NEXT_SLOT_BY_HOST.get(_pacing_key(ep["base_url"], ep["model"]), 0.0) - now)
            out.append({
                "name": ep["name"],
                "model": ep["model"],
                "base_url": ep["base_url"],
                "local": _is_local(ep["base_url"]),
                "status": "cooling_down" if cd > now else ("throttled" if wait > 0.1 else "ready"),
                "cooldown_remaining_sec": round(max(0.0, cd - now), 1),
                "throttle_wait_sec": round(wait, 1),
                "last_error": _LAST_ERROR.get(key, "") if cd > now else "",
                "last_success_at": _LAST_SUCCESS.get(key),
            })
    return out


def check_connections() -> list[dict]:
    """Probe configured endpoints using a tiny prompt, never a user's document."""
    from concurrent.futures import ThreadPoolExecutor

    def probe(endpoint: dict) -> None:
        base, model = endpoint["base_url"], endpoint["model"]
        try:
            _wait_for_slot(base, model)
            _call_endpoint("Reply with exactly OK.", 0.0, base, model, endpoint["api_key"], timeout=20.0, max_tokens=128)
            with _STATE_LOCK:
                _LAST_SUCCESS[_ep_key(base, model)] = time.time()
                _COOLDOWNS.pop(_ep_key(base, model), None)
                _LAST_ERROR.pop(_ep_key(base, model), None)
        except Exception as exc:
            _handle_error(exc, base, model, endpoint["name"])

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(probe, _build_pool()))
    _save_persistent_cooldowns()
    return get_provider_status()


def call_llm_pool(prompt: str, cfg: Optional[HumanizerConfig] = None, temperature: float = 0.9) -> str:
    """Call the first healthy endpoint, failing over across the pool."""
    global _CALL_COUNTER
    pool = _build_pool()
    if not pool:
        raise RuntimeError("No LLM providers configured. Add an API key to .env or .keys.env (see PROVIDERS.md).")

    now = time.time()
    with _STATE_LOCK:
        ready = [e for e in pool if _COOLDOWNS.get(_ep_key(e["base_url"], e["model"]), 0.0) <= now]
        _CALL_COUNTER += 1
        rr = _CALL_COUNTER
    if not ready:
        with _STATE_LOCK:
            soonest = min(pool, key=lambda e: _COOLDOWNS.get(_ep_key(e["base_url"], e["model"]), 0.0))
            wait = _COOLDOWNS.get(_ep_key(soonest["base_url"], soonest["model"]), 0.0) - now
        if wait > 20:
            raise RuntimeError(f"All {len(pool)} LLM endpoints are cooling down (next ready in {wait:.0f}s).")
        time.sleep(max(0.0, wait))
        ready = [soonest]

    # Prefer explicit gateways, then direct endpoints; local Ollama is last.
    def _rank(item: tuple[int, dict]) -> tuple:
        i, ep = item
        priority = 0 if ep["name"] in ("Custom", "OmniRoute", "FreeLLMAPI") else (2 if _is_local(ep["base_url"]) else 1)
        return (priority, round(_provider_wait(ep["base_url"], ep["model"]), 1), (i + rr) % len(ready))

    ordered = [ep for _, ep in sorted(enumerate(ready), key=_rank)]
    last_err: Optional[BaseException] = None
    for ep in ordered:
        if cfg is not None and cfg.cancelled:
            raise RuntimeError("Cancelled")
        name, base, model, key = ep["name"], ep["base_url"], ep["model"], ep["api_key"]
        try:
            if not _is_local(base):
                _wait_for_slot(base, model, cfg)
            if cfg is not None:
                cfg.status(f"Calling {name} ({model})…")
            result = _call_endpoint(prompt, temperature, base, model, key, timeout=120.0 if _is_local(base) else 75.0)
            with _STATE_LOCK:
                _LAST_SUCCESS[_ep_key(base, model)] = time.time()
            return result
        except Exception as e:
            if cfg is not None and cfg.cancelled:
                raise RuntimeError("Cancelled") from e
            last_err = e
            logger.info("%s/%s failed: %s", name, model, e)
            _handle_error(e, base, model, name)
    raise RuntimeError(f"All LLM providers failed. Last error: {last_err}")


__all__ = ["call_llm_pool", "get_provider_status", "reset_cooldowns", "has_providers",
           "available_count", "load_keys_if_needed", "key_file_candidates"]
