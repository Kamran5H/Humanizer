"""
Humanizer configuration.

One dataclass holds every tunable. Unknown legacy fields are still accepted so
older scripts keep working.
"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

MAX_INPUT_CHARS = 500_000


@dataclass
class HumanizerConfig:
    """Master configuration for the humanizer pipeline."""

    # --- Engine selection ---------------------------------------------------
    use_gemini: bool = True        # True = use the LLM provider pool when keys exist (auto-falls back to offline rules)
    scientific: bool = True        # Academic register: no contractions, domain terms untouched

    # --- LLM rewrite controls -----------------------------------------------
    stealth_level: int = 3         # 1 = single rewrite, 2 = best-of-N scored, 3 = best-of-N + sentence refinement
    retry_budget: int = 4          # max candidates per paragraph at level >= 2
    target_pct: float = 10.0       # stop early once the local AI score is at or below this
    gemini_temperature: float = 0.9
    min_length_ratio: float = 0.75  # reject rewrites shorter than this fraction of the source
    max_length_ratio: float = 1.45  # reject rewrites longer than this multiple of the source
    parallel_workers: int = 4

    # --- Offline rule pipeline ----------------------------------------------
    use_phrase_subs: bool = True
    use_contractions: bool = True
    use_burstiness: bool = True
    use_structural: bool = True    # em-dash and structural repairs
    subs_prob: float = 1.0
    contract_prob: float = 0.85
    burst_split_prob: float = 0.85
    min_long_words: int = 22       # sentences at least this long may be split for rhythm

    # --- Legacy fields (accepted, mostly unused) ----------------------------
    swap_prob: float = 0.0
    min_word_len: int = 6
    phrase_prob: float = 1.0
    aside_prob: float = 0.0
    burst_merge_prob: float = 0.0
    imperf_rate: float = 0.0
    fragment_prob: float = 0.0
    rare_syn_bias: float = 0.0
    min_syn_freq: int = 2
    max_short_words: int = 10
    use_asides: bool = False
    use_imperfections: bool = False
    use_sweep: bool = True
    use_locking: bool = True
    double_pass: bool = False
    optimize: bool = False
    optimize_n: int = 8
    optimize_target: float = 8.0
    verify_zerogpt: bool = False

    # --- Runtime hooks ------------------------------------------------------
    cancel_event: Optional[threading.Event] = None
    status_callback: Optional[Callable[[str], None]] = None
    progress_callback: Optional[Callable[[int, int, float, str], None]] = None
    rng: random.Random = field(default_factory=random.Random)
    stats: dict = field(default_factory=dict)  # filled by the engine: {"llm": n, "rules": n, "kept": n}
    _stats_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def count(self, key: str) -> None:
        with self._stats_lock:
            self.stats[key] = self.stats.get(key, 0) + 1

    @property
    def engine_used(self) -> str:
        llm, rules = self.stats.get("llm", 0), self.stats.get("rules", 0)
        if llm and rules:
            return "mixed"
        return "llm" if llm else "rules"

    def __post_init__(self) -> None:
        for name in ("swap_prob", "subs_prob", "phrase_prob", "contract_prob", "aside_prob",
                     "burst_split_prob", "burst_merge_prob", "imperf_rate", "fragment_prob", "rare_syn_bias"):
            val = getattr(self, name)
            if not 0.0 <= val <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.stealth_level not in (1, 2, 3):
            raise ValueError("stealth_level must be 1, 2 or 3")
        if not 0.0 <= self.target_pct <= 100.0:
            raise ValueError("target_pct must be between 0 and 100")
        self.retry_budget = max(1, min(12, int(self.retry_budget)))
        self.parallel_workers = max(1, min(16, int(self.parallel_workers)))

    @property
    def cancelled(self) -> bool:
        return bool(self.cancel_event and self.cancel_event.is_set())

    def status(self, message: str) -> None:
        if self.status_callback:
            try:
                self.status_callback(message)
            except Exception:
                pass

    def report_progress(self, current: int, total: int, message: str = "") -> None:
        """Report progress to progress_callback and status_callback (never raises)."""
        pct = (current / total) * 100.0 if total > 0 else 0.0
        if self.progress_callback:
            try:
                self.progress_callback(current, total, pct, message)
            except Exception:
                pass
        if self.status_callback:
            try:
                msg = f"[{pct:5.1f}%] ({current}/{total}) {message}".strip() if total > 0 else message
                self.status_callback(msg)
            except Exception:
                pass

    def to_dict(self) -> dict:
        return {
            "use_gemini": self.use_gemini,
            "stealth_level": self.stealth_level,
            "target_pct": self.target_pct,
            "scientific": self.scientific,
            "min_length_ratio": self.min_length_ratio,
            "retry_budget": self.retry_budget,
        }
