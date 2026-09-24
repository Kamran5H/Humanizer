"""
Humanizer Pro Package.
High-Performance Stealth Text Humanizer & Academic Rewriter.
"""

from humanizer.config import HumanizerConfig
from humanizer.detector import score_text, sentence_perplexity, smooth_sentences, split_sentences
from humanizer.engine import (
    humanize_text,
    humanize_docx,
    humanize_paragraph_stealth,
    humanize_paragraph_rules,
)
from humanizer.document import extract_text_from_file, extract_text_from_pdf
from humanizer.lint import lint_text, is_clean, readability
from humanizer.providers import call_llm_pool, get_provider_status, reset_cooldowns
from humanizer.web import launch_web

__version__ = "5.1.0"
__all__ = [
    "HumanizerConfig",
    "score_text",
    "sentence_perplexity",
    "smooth_sentences",
    "split_sentences",
    "humanize_text",
    "humanize_docx",
    "humanize_paragraph_stealth",
    "humanize_paragraph_rules",
    "extract_text_from_file",
    "extract_text_from_pdf",
    "lint_text",
    "is_clean",
    "readability",
    "call_llm_pool",
    "get_provider_status",
    "reset_cooldowns",
    "launch_web",
]
