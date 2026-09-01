"""
Persistent Multi-Provider LLM Pool and Rate-Limiter Module.
Handles token-bucket throttling, persistent on-disk cooldowns, multi-cloud failover,
and thread-safe endpoint selection across Groq, Cerebras, SambaNova, OpenRouter,
GitHub Models, Gemini OpenAI-compatible endpoints, and local Ollama.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from humanizer.config import HumanizerConfig

logger = logging.getLogger(__name__)

# State persistence directory
_STATE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "humanizer_pro"
_STATE_FILE = _STATE_DIR / "provider_state.json"

_THROTTLE_LOCK = threading.Lock()
_LAST_CALL_BY_HOST: dict[str, float] = {}
_MIN_INTERVAL = 3.5  # Base seconds between outbound calls to same host

# Guards the cooldown/exhaustion state below, which is read and written from
# multiple worker threads (the pool advertises thread-safe endpoint selection).
_STATE_LOCK = threading.RLock()
_OAI_COOLDOWNS: dict[str, float] = {}
_OAI_TPD_HIT: set[str] = set()
_GEMINI_EXHAUSTED_UNTIL = [0.0]

_KEYS_LOADED = False

_FREE_PROVIDER_REGISTRY: list[tuple[str, str, str, str]] = [
    # (env_var, base_url, default_models_csv, display_name)
    ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash,gemini-2.5-flash-lite", "Gemini"),
    ("SAMBANOVA_API_KEY", "https://api.sambanova.ai/v1", "Meta-Llama-3.3-70B-Instruct", "SambaNova"),
    ("GROQ_API_KEY", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile,llama-3.1-8b-instant", "Groq"),
]


def load_keys_if_needed() -> None:
    global _KEYS_LOADED
    if _KEYS_LOADED:
        return
    for p in (
        Path(__file__).parent.parent / ".keys.env",
        Path(__file__).parent.parent.parent / ".keys.env",
        Path.cwd() / ".keys.env",
    ):
        if p.exists():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.strip().startswith("#"):
                        k, v = line.strip().split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
            except Exception:
                pass
            break
    _load_persistent_cooldowns()
    _KEYS_LOADED = True


def _load_persistent_cooldowns() -> None:
    if not _STATE_FILE.exists():
        return
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        now = time.time()
        cooldowns = data.get("cooldowns", {})
        with _STATE_LOCK:
            for ep, ts in cooldowns.items():
                if ts > now:
                    _OAI_COOLDOWNS[ep] = ts
            _GEMINI_EXHAUSTED_UNTIL[0] = data.get("gemini_exhausted_until", 0.0)
    except Exception as e:
        logger.debug(f"Failed to load persistent cooldown state: {e}")


def _save_persistent_cooldowns() -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        with _STATE_LOCK:
            # snapshot under lock so a concurrent mutation can't corrupt json.dumps
            payload = {
                "cooldowns": dict(_OAI_COOLDOWNS),
                "gemini_exhausted_until": _GEMINI_EXHAUSTED_UNTIL[0],
                "updated_at": time.time(),
            }
        _STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug(f"Failed to save persistent cooldown state: {e}")


def reset_cooldowns() -> None:
    """Clear all persistent and in-memory cooldowns."""
    with _STATE_LOCK:
        _OAI_COOLDOWNS.clear()
        _OAI_TPD_HIT.clear()
        _GEMINI_EXHAUSTED_UNTIL[0] = 0.0
    _save_persistent_cooldowns()


def _host_key(url: str) -> str:
    import urllib.parse
    try:
        p = urllib.parse.urlparse(url)
        return p.netloc or url
    except Exception:
        return url


def _provider_wait(base_url: str) -> float:
    host = _host_key(base_url)
    with _THROTTLE_LOCK:
        last = _LAST_CALL_BY_HOST.get(host, 0.0)
        return max(0.0, _MIN_INTERVAL - (time.time() - last))


def _throttle(base_url: str) -> None:
    host = _host_key(base_url)
    with _THROTTLE_LOCK:
        last = _LAST_CALL_BY_HOST.get(host, 0.0)
        wait = _MIN_INTERVAL - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL_BY_HOST[host] = time.time()


def _ep_key(base_url: str, model: str) -> str:
    return f"{base_url}||{model}"


def _cool_endpoint(base_url: str, model: str, seconds: float) -> None:
    with _STATE_LOCK:
        _OAI_COOLDOWNS[_ep_key(base_url, model)] = time.time() + seconds
    _save_persistent_cooldowns()


def _build_pool(primary_base: Optional[str] = None) -> list[dict]:
    load_keys_if_needed()
    pool: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, base: str, model: str, key: str) -> None:
        k = (base, model)
        if not base or not model or not key or k in seen:
            return
        seen.add(k)
        pool.append({"name": name, "base_url": base, "model": model, "api_key": key})

    # First add registry providers (Gemini first for top quality and fast response)
    for env_k, base_url, models_csv, display in _FREE_PROVIDER_REGISTRY:
        k = os.environ.get(env_k, "").strip()
        if k:
            override = os.environ.get(env_k.replace("_API_KEY", "_MODELS").replace("_TOKEN", "_MODELS"), "").strip()
            models = [x.strip() for x in (override or models_csv).split(",") if x.strip()]
            for m in models:
                add(display, base_url, m, k)

    base = (primary_base or os.environ.get("STEALTH_API_BASE", "")).strip()
    if base:
        key = os.environ.get("STEALTH_API_KEY", "").strip() or "ollama"
        models_raw = os.environ.get("STEALTH_MODEL", "llama-3.3-70b-versatile,llama-3.1-8b-instant")
        for m in [x.strip() for x in models_raw.split(",") if x.strip()]:
            add("Primary", base, m, key)

    fb = os.environ.get("STEALTH_FALLBACK_BASE", "").strip()
    if fb:
        fb_model = os.environ.get("STEALTH_FALLBACK_MODEL", "qwen2.5:1.5b").strip()
        fb_key = os.environ.get("STEALTH_FALLBACK_KEY", "ollama").strip() or "ollama"
        add("Local (Ollama)", fb, fb_model, fb_key)

    return pool


def _call_endpoint(prompt: str, temperature: float, base_url: str, model: str, api_key: str, timeout: float = 90.0) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1800,
    }
    if "openrouter.ai" in base_url:
        payload["include_reasoning"] = False

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HumanizerPro/5.0",
            "HTTP-Referer": "https://localhost",
            "X-Title": "Humanizer Pro",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))

    choices = d.get("choices", [])
    if not choices:
        raise ValueError(f"Empty choices in LLM response from {base_url} ({model})")

    msg = choices[0].get("message", {})
    raw_content = msg.get("content")

    # If content is a list of parts (multimodal/openrouter format), extract text parts
    if isinstance(raw_content, list):
        parts = []
        for p in raw_content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text", ""))
            elif isinstance(p, str):
                parts.append(p)
        raw_content = "".join(parts)

    return (raw_content or "").strip()


def _handle_error(e: BaseException, base_url: str, model: str, provider: str) -> None:
    if isinstance(e, urllib.error.HTTPError):
        code = e.code
        if code == 429:
            hdr = e.headers.get("retry-after")
            wait = float(hdr) if hdr and hdr.isdigit() else 35.0
            _cool_endpoint(base_url, model, wait)
            logger.warning(f"{provider}/{model} rate-limited. Cooldown set for {wait:.0f}s.")
            return
        if code in (400, 404):
            _cool_endpoint(base_url, model, 86400.0)
            logger.warning(f"{provider}/{model} unavailable ({code}). Disabled for 24h.")
            return
        if code in (401, 403):
            _cool_endpoint(base_url, model, 86400.0)
            logger.warning(f"{provider}/{model} auth failed ({code}). Check API key.")
            return
    _cool_endpoint(base_url, model, 15.0)


def call_llm_pool(prompt: str, cfg: Optional[HumanizerConfig] = None, temperature: float = 1.0) -> str:
    """Execute LLM call across the provider pool with automatic failover."""
    pool = _build_pool()
    if not pool:
        raise RuntimeError("No LLM providers configured. Please add an API key to .keys.env")

    now = time.time()
    available = [e for e in pool if now >= _OAI_COOLDOWNS.get(_ep_key(e["base_url"], e["model"]), 0.0)]
    if not available:
        available = pool[:1]

    def _sort_key(ep):
        base = ep["base_url"]
        is_local = "localhost" in base or "127.0.0.1" in base
        return 0.0 if is_local else _provider_wait(base)

    available = sorted(available, key=_sort_key)
    last_err: Optional[Exception] = None

    for ep in available:
        name, base, model, key = ep["name"], ep["base_url"], ep["model"], ep["api_key"]
        is_local = "localhost" in base or "127.0.0.1" in base
        try:
            if not is_local:
                _throttle(base)
            if cfg and cfg.status_callback:
                cfg.status_callback(f"Calling {name} ({model})...")
            return _call_endpoint(prompt, temperature, base, model, key)
        except Exception as e:
            last_err = e
            _handle_error(e, base, model, name)
            continue

    if last_err is not None:
        raise last_err
    raise RuntimeError("All LLM providers in the pool failed.")
