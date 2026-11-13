"""
Humanizer Pro launcher (backwards compatible).

    python humanizer_pro.py                 # web studio (opens your browser)
    python humanizer_pro.py --gui           # desktop app (Tkinter)
    python humanizer_pro.py essay.docx      # command line — see --help
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from humanizer.config import HumanizerConfig  # noqa: E402,F401
from humanizer.detector import analyze, score_text, sentence_perplexity, smooth_sentences, split_sentences, warmup  # noqa: E402,F401
from humanizer.engine import (  # noqa: E402,F401
    build_stealth_prompt,
    humanize_docx,
    humanize_paragraph_rules,
    humanize_paragraph_stealth,
    humanize_text,
)
from humanizer.lint import is_clean, lint_text, readability  # noqa: E402,F401
from humanizer.providers import call_llm_pool, load_keys_if_needed, reset_cooldowns  # noqa: E402,F401
from humanizer.rules import (  # noqa: E402,F401
    DEFAULT_CONTRACTIONS,
    DEFAULT_SUBSTITUTIONS,
    apply_contractions,
    apply_phrase_subs,
    apply_word_subs,
    ensure_nltk_data,
    fix_grammar,
    lock_elements,
    unlock_elements,
)

# Legacy aliases
humanize_paragraph_gemini_stealth = humanize_paragraph_stealth
humanize_paragraph = humanize_paragraph_stealth


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--web" in argv or not argv:
        from humanizer.web import launch_web

        open_browser = "--no-browser" not in argv
        port = 8000
        if "--port" in argv:
            port = int(argv[argv.index("--port") + 1])
        launch_web(port=port, open_browser=open_browser)
        return 0
    if "--gui" in argv:
        from humanizer.gui import launch_gui

        launch_gui()
        return 0
    from humanizer.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
