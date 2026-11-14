"""
Multi-provider LLM pool with automatic failover.

* Discovers every OpenAI-compatible provider whose key is present in the
  environment, ``.env`` or ``.keys.env`` (Gemini, Groq, Cerebras, SambaNova,
  OpenRouter, GitHub Models, Together, Mistral, DeepInfra, plus any custom
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
_KEYS_LOADED = False
_CALL_COUNTER = 0
_FILE_KEYS: set[str] = set()  # names defined explicitly in a .env / .keys.env file

# Backwards-compatible aliases for older scripts/tests.
_OAI_COOLDOWNS = _COOLDOWNS

# (env var, base url, default models csv, display name)
_FREE_PROVIDER_REGISTRY: list[tuple[str, str, str, str]] = [
    ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash,gemini-2.5-flash-lite", "Gemini"),
    ("GROQ_API_KEY", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile,llama-3.1-8b-instant", "Groq"),
    ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1", "llama-3.3-70b", "Cerebras"),
    ("SAMBANOVA_API_KEY", "https://api.sambanova.ai/v1", "Meta-Llama-3.3-70B-Instruct", "SambaNova"),
    ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free,deepseek/deepseek-chat-v3-0324:free,mistralai/mistral-small-3.2-24b-instruct:free", "OpenRouter"),
    ("GITHUB_MODELS_TOKEN", "https://models.inference.ai.azure.com", "gpt-4o-mini,Meta-Llama-3.3-70B-Instruct", "GitHub Models"),
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
        models_raw = os.environ.get("STEALTH_MODEL", "").strip() or "llama-3.3-70b-versatile"
        for m in [x.strip() for x in models_raw.split(",") if x.strip()]:
            add("Custom", base, m, key)

    for env_k, base_url, models_csv, display in _FREE_PROVIDER_REGISTRY:
        key = os.environ.get(env_k, "").strip()
        if not key and env_k == "GITHUB_MODELS_TOKEN" and "GITHUB_TOKEN" in _FILE_KEYS:
            # A generic GITHUB_TOKEN from the shell/CI is NOT used implicitly (privacy);
            # only one the user placed in a key file on purpose.
            key = os.environ.get("GITHUB_TOKEN", "").strip()
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


def available_count() -> int:
    now = time.time()
    with _STATE_LOCK:
        return sum(1 for e in _build_pool() if _COOLDOWNS.get(_ep_key(e["base_url"], e["model"]), 0.0) <= now)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _reserve_slot(base_url: str) -> float:
    """Reserve the next call slot for a host and return how long to wait (lock is not held while sleeping)."""
    host = _host_key(base_url)
    with _STATE_LOCK:
        now = time.time()
        slot = max(now, _NEXT_SLOT_BY_HOST.get(host, 0.0))
        _NEXT_SLOT_BY_HOST[host] = slot + _MIN_INTERVAL
        return slot - now


def _provider_wait(base_url: str) -> float:
    with _STATE_LOCK:
        return max(0.0, _NEXT_SLOT_BY_HOST.get(_host_key(base_url), 0.0) - time.time())


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
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "HumanizerPro/6.0",
            "HTTP-Referer": "https://github.com/Kamran5H/Humanizer",
            "X-Title": "Humanizer Pro",
        },
    )
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
            wait = max(0.0, _NEXT_SLOT_BY_HOST.get(_host_key(ep["base_url"]), 0.0) - now)
            out.append({
                "name": ep["name"],
                "model": ep["model"],
                "base_url": ep["base_url"],
                "local": _is_local(ep["base_url"]),
                "status": "cooling_down" if cd > now else ("throttled" if wait > 0.1 else "ready"),
                "cooldown_remaining_sec": round(max(0.0, cd - now), 1),
                "throttle_wait_sec": round(wait, 1),
                "last_error": _LAST_ERROR.get(key, "") if cd > now else "",
            })
    return out


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

    # Prefer cloud endpoints with no pending wait, rotate between equals, local last.
    def _rank(item: tuple[int, dict]) -> tuple:
        i, ep = item
        return (_is_local(ep["base_url"]), round(_provider_wait(ep["base_url"]), 1), (i + rr) % len(ready))

    ordered = [ep for _, ep in sorted(enumerate(ready), key=_rank)]
    last_err: Optional[BaseException] = None
    for ep in ordered:
        if cfg is not None and cfg.cancelled:
            raise RuntimeError("Cancelled")
        name, base, model, key = ep["name"], ep["base_url"], ep["model"], ep["api_key"]
        try:
            if not _is_local(base):
                delay = _reserve_slot(base)
                if delay > 0:
                    time.sleep(delay)
            if cfg is not None:
                cfg.status(f"Calling {name} ({model})…")
            return _call_endpoint(prompt, temperature, base, model, key, timeout=120.0 if _is_local(base) else 75.0)
        except Exception as e:
            last_err = e
            logger.info("%s/%s failed: %s", name, model, e)
            _handle_error(e, base, model, name)
    raise RuntimeError(f"All LLM providers failed. Last error: {last_err}")


__all__ = ["call_llm_pool", "get_provider_status", "reset_cooldowns", "has_providers",
           "available_count", "load_keys_if_needed", "key_file_candidates"]
