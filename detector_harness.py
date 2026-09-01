"""
Real-detector verification harness.

Submits text to third-party AI detectors and reads the actual AI percentage, so
the humanizer's "<=5%" claim is verified against the same scorers a grader uses —
not only the offline gauge in ai_detector.py.

Backends, by preference:
  • ZeroGPT   — free public endpoint, no key. Rate-limited per IP (a handful of
                calls, then "make a purchase"). Use sparingly: final validation,
                not the inner optimize loop.
  • Sapling   — needs SAPLING_API_KEY    (free tier available)
  • GPTZero   — needs GPTZERO_API_KEY
  • Originality — needs ORIGINALITY_API_KEY (paid)
  • local     — ai_detector.py offline gauge, always available, never blocks.

Keys are read from .keys.env (KEY=VALUE per line) or the environment.

Each backend returns ai_pct in 0..100 (higher = more AI-looking) or None when it
could not score (no key / throttled / error). Honest by construction: a detector
that did not answer is reported as "unavailable", never silently treated as a pass.

CLI:
    python detector_harness.py "<text>"           # score one string on all backends
    python detector_harness.py --file note.txt
    python detector_harness.py --selftest         # score the built-in AI sample
    python detector_harness.py --benchmark        # before/after corpus, FAIL if >5%
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

TARGET_PCT = 5.0
_KEYS_LOADED = False


def load_keys() -> None:
    global _KEYS_LOADED
    if _KEYS_LOADED:
        return
    for p in (Path(__file__).parent / ".keys.env",
              Path(__file__).parent.parent / ".keys.env",
              Path.cwd() / ".keys.env"):
        if p.exists():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.strip().startswith("#"):
                        k, v = line.strip().split("=", 1)
                        os.environ.setdefault(k, v)
            except Exception:
                pass
            break
    _KEYS_LOADED = True


@dataclass
class DetectorResult:
    name: str
    ai_pct: Optional[float]   # 0..100, or None if unscored
    ok: bool                  # True if a real score came back
    note: str = ""

    def __str__(self) -> str:
        if self.ok and self.ai_pct is not None:
            flag = "PASS" if self.ai_pct <= TARGET_PCT else "FAIL"
            return f"{self.name:12} {self.ai_pct:5.1f}%  [{flag}]"
        return f"{self.name:12}   n/a   ({self.note})"


def _post_json(url: str, payload: dict, headers: dict, timeout: float = 30.0) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# --------------------------------------------------------------------------- #
# Individual backends. Each returns DetectorResult.
# --------------------------------------------------------------------------- #

def score_zerogpt(text: str, retries: int = 0, backoff: float = 20.0) -> DetectorResult:
    """Free public endpoint. Throttles per IP after a few calls ("make a purchase").

    The throttle clears within ~20-60s. With `retries` > 0 the call waits `backoff`
    seconds (growing) and retries when throttled, so it can be used to gate a
    re-roll loop. Default retries=0 keeps single-shot scoring fast.
    """
    name = "ZeroGPT"
    if len(text.split()) < 20:
        return DetectorResult(name, None, False, "text too short for ZeroGPT")
    last = DetectorResult(name, None, False, "unknown")
    for attempt in range(retries + 1):
        try:
            d = _post_json(
                "https://api.zerogpt.com/api/detect/detectText",
                {"input_text": text},
                {"Content-Type": "application/json",
                 "Origin": "https://www.zerogpt.com",
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            if not d.get("success"):
                last = DetectorResult(name, None, False,
                                      str(d.get("message", "throttled"))[:40])
            else:
                data = d.get("data") or {}
                pct = data.get("fakePercentage")
                if pct is None:
                    return DetectorResult(name, None, False, "no fakePercentage")
                return DetectorResult(name, float(pct), True)
        except urllib.error.HTTPError as e:
            last = DetectorResult(name, None, False, f"HTTP {e.code} (throttled)")
        except Exception as e:
            last = DetectorResult(name, None, False, f"{type(e).__name__}")
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    return last


def score_sapling(text: str) -> DetectorResult:
    name = "Sapling"
    key = os.environ.get("SAPLING_API_KEY")
    if not key:
        return DetectorResult(name, None, False, "no SAPLING_API_KEY")
    try:
        d = _post_json("https://api.sapling.com/api/v1/aidetect",
                       {"key": key, "text": text},
                       {"Content-Type": "application/json"})
        score = d.get("score")
        if score is None:
            return DetectorResult(name, None, False, "no score field")
        return DetectorResult(name, float(score) * 100.0, True)
    except Exception as e:
        return DetectorResult(name, None, False, f"{type(e).__name__}")


def score_gptzero(text: str) -> DetectorResult:
    name = "GPTZero"
    key = os.environ.get("GPTZERO_API_KEY")
    if not key:
        return DetectorResult(name, None, False, "no GPTZERO_API_KEY")
    try:
        d = _post_json("https://api.gptzero.me/v2/predict/text",
                       {"document": text},
                       {"Content-Type": "application/json",
                        "x-api-key": key, "Accept": "application/json"})
        docs = d.get("documents") or []
        if not docs:
            return DetectorResult(name, None, False, "no documents")
        doc = docs[0]
        cp = doc.get("class_probabilities") or {}
        pct = cp.get("ai")
        if pct is None:
            pct = doc.get("completely_generated_prob")
        if pct is None:
            return DetectorResult(name, None, False, "no probability field")
        return DetectorResult(name, float(pct) * 100.0, True)
    except Exception as e:
        return DetectorResult(name, None, False, f"{type(e).__name__}")


def score_originality(text: str) -> DetectorResult:
    name = "Originality"
    key = os.environ.get("ORIGINALITY_API_KEY")
    if not key:
        return DetectorResult(name, None, False, "no ORIGINALITY_API_KEY")
    try:
        d = _post_json("https://api.originality.ai/api/v1/scan/ai",
                       {"content": text},
                       {"Content-Type": "application/json", "X-OAI-API-KEY": key})
        score = (d.get("score") or {}).get("ai")
        if score is None:
            return DetectorResult(name, None, False, "no score.ai field")
        return DetectorResult(name, float(score) * 100.0, True)
    except Exception as e:
        return DetectorResult(name, None, False, f"{type(e).__name__}")


_LOCAL = None
_LOCAL_TRIED = False


def score_local(text: str) -> DetectorResult:
    """Offline gauge from ai_detector.py — always available, used to gate re-rolls."""
    global _LOCAL, _LOCAL_TRIED
    name = "local-gauge"
    if not _LOCAL_TRIED:
        _LOCAL_TRIED = True
        try:
            import ai_detector
            _LOCAL = ai_detector
        except Exception as e:
            _LOCAL = None
            return DetectorResult(name, None, False, f"unavailable: {type(e).__name__}")
    if _LOCAL is None:
        return DetectorResult(name, None, False, "unavailable")
    try:
        s = _LOCAL.score_text(text)
        return DetectorResult(name, float(s.get("ai_score", 0.0)), True)
    except Exception as e:
        return DetectorResult(name, None, False, f"{type(e).__name__}")


# Real third-party detectors (exclude local gauge).
REAL_BACKENDS: list[Callable[[str], DetectorResult]] = [
    score_zerogpt, score_sapling, score_gptzero, score_originality,
]
ALL_BACKENDS = REAL_BACKENDS + [score_local]


def score_all(text: str, include_local: bool = True,
              real_only: bool = False) -> list[DetectorResult]:
    load_keys()
    backends = REAL_BACKENDS if real_only else (
        ALL_BACKENDS if include_local else REAL_BACKENDS)
    return [b(text) for b in backends]


def real_scores(results: list[DetectorResult]) -> list[float]:
    return [r.ai_pct for r in results
            if r.ok and r.ai_pct is not None and r.name != "local-gauge"]


# --------------------------------------------------------------------------- #
# Benchmark: before/after over the corpus, FAIL if median or max > target.
# --------------------------------------------------------------------------- #

def _load_corpus() -> list[dict]:
    p = Path(__file__).parent / "stealth_eval" / "corpus.json"
    return json.loads(p.read_text(encoding="utf-8"))


def benchmark(humanize_fn: Callable[[str], str], use_real: bool = True,
              throttle_s: float = 4.0, limit: Optional[int] = None) -> int:
    """Score every corpus sample before/after humanizing. Returns process exit code.

    `humanize_fn` maps a paragraph to its humanized form. Reports the real ZeroGPT AI%
    before and after for each sample, plus the offline gauge. Builds FAIL when the
    real-detector median or max exceeds the target.
    """
    corpus = _load_corpus()
    if limit:
        corpus = corpus[:limit]
    print(f"\n=== STEALTH BENCHMARK  (target <= {TARGET_PCT}% AI) ===")
    print("Real detector: ZeroGPT (free, throttle-retried). "
          "Add SAPLING_API_KEY / GPTZERO_API_KEY / ORIGINALITY_API_KEY for more.\n")
    before_real: list[float] = []
    after_real: list[float] = []
    after_local: list[float] = []
    real_seen: set[str] = set()

    for item in corpus:
        sid, src = item["id"], item["text"]
        bl = score_local(src)
        b_zg = score_zerogpt(src, retries=2, backoff=15.0)
        if b_zg.ok:
            before_real.append(b_zg.ai_pct)
        out = humanize_fn(src)
        al = score_local(out)
        after_local.append(al.ai_pct or 0.0)
        time.sleep(throttle_s)
        a_zg = score_zerogpt(out, retries=3, backoff=15.0)
        a_real = []
        if a_zg.ok:
            after_real.append(a_zg.ai_pct); real_seen.add("ZeroGPT"); a_real.append(a_zg)
        # Keyed detectors, if configured.
        for fn in (score_sapling, score_gptzero, score_originality):
            r = fn(out)
            if r.ok and r.ai_pct is not None:
                after_real.append(r.ai_pct); real_seen.add(r.name); a_real.append(r)

        bz = f"{b_zg.ai_pct:.0f}%" if b_zg.ok else "n/a"
        az = "  ".join(f"{r.name}:{r.ai_pct:.0f}%" for r in a_real) or "(no real detector)"
        flag = ""
        if a_zg.ok:
            flag = " [PASS]" if a_zg.ai_pct <= TARGET_PCT else " [FAIL]"
        print(f"[{sid:11}] ZeroGPT {bz} -> {az}{flag}   | gauge {bl.ai_pct:.0f}%->{al.ai_pct:.0f}%")

    print("\n--- summary ---")
    if after_local:
        print(f"local-gauge   median {statistics.median(after_local):.1f}%  "
              f"max {max(after_local):.1f}%")
    if after_real:
        med, mx = statistics.median(after_real), max(after_real)
        verdict = "PASS" if (med <= TARGET_PCT and mx <= TARGET_PCT) else "FAIL"
        bmed = statistics.median(before_real) if before_real else float("nan")
        print(f"real ({','.join(sorted(real_seen))})  before median {bmed:.1f}%  "
              f"-> after median {med:.1f}%  max {mx:.1f}%  -> {verdict}")
        return 0 if verdict == "PASS" else 1
    print("real detectors: UNVERIFIED — none reachable this run "
          "(ZeroGPT throttled and no API keys). <=5% NOT proven; gauge only.")
    return 2


def _print(results: list[DetectorResult]) -> None:
    for r in results:
        print("  " + str(r))
    reals = real_scores(results)
    if reals:
        print(f"  -> real median {statistics.median(reals):.1f}%  max {max(reals):.1f}%")


if __name__ == "__main__":
    args = sys.argv[1:]
    load_keys()
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    if args[0] == "--selftest":
        sample = _load_corpus()[0]["text"]
        print("Scoring built-in AI sample on all backends:")
        _print(score_all(sample))
        sys.exit(0)
    if args[0] == "--benchmark":
        import humanizer_pro as H
        H.load_keys_if_needed()
        limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        level = 3  # ZeroGPT-gated — what actually proves <=5%
        cfg = H.HumanizerConfig(use_gemini=True, stealth_level=level, retry_budget=3,
                                target_pct=TARGET_PCT, gemini_temperature=1.0,
                                double_pass=False)
        sys.exit(benchmark(lambda t: H.humanize_paragraph_gemini_stealth(t, cfg),
                           limit=limit))
    if args[0] == "--file":
        text = Path(args[1]).read_text(encoding="utf-8")
    else:
        text = args[0]
    _print(score_all(text))
