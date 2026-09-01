"""
Local AI Detector — Backwards Compatibility Bridge.
Exports score_text, sentence_perplexity, smooth_sentences, split_sentences, warmup.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from humanizer.detector import (
    score_text,
    sentence_perplexity,
    smooth_sentences,
    split_sentences,
    warmup,
)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        sample = Path(sys.argv[1]).read_text(encoding="utf-8")
    else:
        sample = (
            "Artificial intelligence has emerged as a transformative force in "
            "healthcare, revolutionizing the way medical professionals diagnose, "
            "treat, and manage patient care. The integration of AI technologies has "
            "demonstrated remarkable potential in enhancing clinical outcomes, "
            "optimizing operational efficiency, and reducing overall costs."
        )
    warmup()
    print(score_text(sample))
