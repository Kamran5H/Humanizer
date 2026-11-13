"""
Humanizer Pro — rewrite AI-sounding text into natural prose, with a local,
explainable AI-likelihood scorer and format-preserving DOCX support.
"""

__version__ = "6.0.0"

from humanizer.config import HumanizerConfig  # noqa: E402
from humanizer.detector import analyze, score_text, sentence_perplexity, smooth_sentences, split_sentences  # noqa: E402
from humanizer.document import extract_text_from_file, extract_text_from_pdf  # noqa: E402
from humanizer.engine import (  # noqa: E402
    check_fidelity,
    humanize_docx,
    humanize_paragraph_rules,
    humanize_paragraph_stealth,
    humanize_text,
)
from humanizer.lint import is_clean, lint_text, readability  # noqa: E402
from humanizer.patterns import find_tells  # noqa: E402
from humanizer.providers import call_llm_pool, get_provider_status, has_providers, reset_cooldowns  # noqa: E402


def launch_web(*args, **kwargs):
    """Start the web studio (imports FastAPI lazily so the core works without it)."""
    from humanizer.web import launch_web as _launch

    return _launch(*args, **kwargs)


__all__ = [
    "__version__",
    "HumanizerConfig",
    "analyze",
    "score_text",
    "find_tells",
    "sentence_perplexity",
    "smooth_sentences",
    "split_sentences",
    "humanize_text",
    "humanize_docx",
    "humanize_paragraph_stealth",
    "humanize_paragraph_rules",
    "check_fidelity",
    "extract_text_from_file",
    "extract_text_from_pdf",
    "lint_text",
    "is_clean",
    "readability",
    "call_llm_pool",
    "get_provider_status",
    "has_providers",
    "reset_cooldowns",
    "launch_web",
]
