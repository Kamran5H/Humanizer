"""
Humanizer Pro v5.0 — High-Performance Stealth AI Rewriting & Detection Bypass Suite.

Backwards-compatible wrapper module that exposes all legacy functions, classes,
and configurations while dispatching to the modular humanizer package.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure Humanizer directory is on sys.path
_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from humanizer.config import HumanizerConfig
from humanizer.detector import (
    score_text,
    sentence_perplexity,
    smooth_sentences,
    split_sentences,
    warmup,
)
from humanizer.engine import (
    humanize_text,
    humanize_docx,
    humanize_paragraph_stealth,
    humanize_paragraph_rules,
    build_stealth_prompt,
)
from humanizer.lint import lint_text, is_clean, readability
from humanizer.providers import call_llm_pool
from humanizer.rules import (
    DEFAULT_SUBSTITUTIONS,
    DEFAULT_CONTRACTIONS,
    lock_elements,
    unlock_elements,
    apply_phrase_subs,
    apply_word_subs,
    apply_contractions,
    fix_grammar,
    ensure_nltk_data,
)
from humanizer.cli import main as cli_main
from humanizer.gui import HumanizerApp, launch_gui

# Legacy alias compatibility
humanize_paragraph_gemini_stealth = humanize_paragraph_stealth
humanize_paragraph = humanize_paragraph_stealth
get_detector = lambda: sys.modules.get("humanizer.detector") or sys.modules.get("ai_detector")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(cli_main())
    else:
        launch_gui()
