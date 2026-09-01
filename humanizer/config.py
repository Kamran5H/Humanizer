"""
Humanizer Configuration Module.
Defines all tunable hyperparameters, flags, and runtime settings.
"""

from __future__ import annotations

import os
import random
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

# Tunable defaults
DEFAULT_SWAP_PROB = 0.0          # OFF by default: WordNet adjective swapping
DEFAULT_MIN_WORD_LEN = 6
DEFAULT_SUBS_PROB = 0.98
DEFAULT_PHRASE_PROB = 0.99
DEFAULT_CONTRACT_PROB = 0.85
DEFAULT_ASIDE_PROB = 0.70
DEFAULT_BURST_SPLIT_PROB = 0.85  # probability to split a long sentence
DEFAULT_BURST_MERGE_PROB = 0.55  # probability to merge two consecutive short sentences
DEFAULT_IMPERF_RATE = 0.15       # ~15% of sentences get a minor imperfection
DEFAULT_FRAGMENT_PROB = 0.18     # probability to add a trailing fragment sentence
DEFAULT_RARE_SYN_BIAS = 0.35     # moderate rarity — avoid archaic/zero-usage words
DEFAULT_MIN_LONG_WORDS = 12      # sentences >= this length are candidates for splitting
DEFAULT_MAX_SHORT_WORDS = 10     # sentences <= this length are candidates for merging
DEFAULT_MIN_SYN_FREQ = 2         # minimum SemCor corpus count — filters out archaic words
MAX_INPUT_CHARS = 500_000


@dataclass
class HumanizerConfig:
    """Master configuration dataclass for the Humanizer pipeline."""
    swap_prob: float = DEFAULT_SWAP_PROB
    min_word_len: int = DEFAULT_MIN_WORD_LEN
    subs_prob: float = DEFAULT_SUBS_PROB
    phrase_prob: float = DEFAULT_PHRASE_PROB
    contract_prob: float = DEFAULT_CONTRACT_PROB
    aside_prob: float = DEFAULT_ASIDE_PROB
    burst_split_prob: float = DEFAULT_BURST_SPLIT_PROB
    burst_merge_prob: float = DEFAULT_BURST_MERGE_PROB
    imperf_rate: float = DEFAULT_IMPERF_RATE
    fragment_prob: float = DEFAULT_FRAGMENT_PROB
    rare_syn_bias: float = DEFAULT_RARE_SYN_BIAS
    min_syn_freq: int = DEFAULT_MIN_SYN_FREQ
    min_long_words: int = DEFAULT_MIN_LONG_WORDS
    max_short_words: int = DEFAULT_MAX_SHORT_WORDS

    use_phrase_subs: bool = True
    use_contractions: bool = True
    use_asides: bool = True
    use_burstiness: bool = True
    use_structural: bool = True
    use_imperfections: bool = True
    use_sweep: bool = True
    use_locking: bool = True
    double_pass: bool = False

    # AI backend selection
    use_gemini: bool = True       # True = use LLM provider pool (Groq/Gemini/Cerebras/Ollama)

    # Local best-of-N optimization (offline rule pipeline mode)
    optimize: bool = False
    optimize_n: int = 8
    optimize_target: float = 8.0

    # Stealth-writer controls
    stealth_level: int = 3        # 1 = single rewrite, 2 = best-of-N offline gauge, 3 = best-of-N + ZeroGPT/refinement
    retry_budget: int = 6         # Max variants per paragraph with guided reflexion
    target_pct: float = 5.0       # Early-stop target AI score %
    gemini_temperature: float = 1.0
    verify_zerogpt: bool = False  # Gate on live ZeroGPT endpoint

    # Academic & scientific register
    scientific: bool = True       # Formal academic prose, exact domain terminology, no contractions or casual asides

    # Content-preservation guard
    min_length_ratio: float = 0.85

    # Parallel concurrency
    parallel_workers: int = 4

    # Cooperative cancellation & Progress reporting
    cancel_event: Optional[threading.Event] = None
    status_callback: Optional[Callable[[str], None]] = None
    progress_callback: Optional[Callable[[int, int, float, str], None]] = None  # (current, total, percentage, message)

    rng: random.Random = field(default_factory=random.Random)

    def report_progress(self, current: int, total: int, message: str = "") -> None:
        """Report live progress to both progress_callback and status_callback."""
        pct = (current / max(1, total)) * 100.0 if total > 0 else 0.0
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

    def __post_init__(self) -> None:
        for name, val in [
            ("swap_prob", self.swap_prob),
            ("subs_prob", self.subs_prob),
            ("phrase_prob", self.phrase_prob),
            ("contract_prob", self.contract_prob),
            ("aside_prob", self.aside_prob),
            ("burst_split_prob", self.burst_split_prob),
            ("burst_merge_prob", self.burst_merge_prob),
            ("imperf_rate", self.imperf_rate),
            ("fragment_prob", self.fragment_prob),
            ("rare_syn_bias", self.rare_syn_bias),
        ]:
            if not 0.0 <= val <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")

    def to_dict(self) -> dict:
        return {
            "use_gemini": self.use_gemini,
            "stealth_level": self.stealth_level,
            "target_pct": self.target_pct,
            "scientific": self.scientific,
            "double_pass": self.double_pass,
            "optimize": self.optimize,
            "min_length_ratio": self.min_length_ratio,
            "retry_budget": self.retry_budget,
            "parallel_workers": self.parallel_workers,
        }
