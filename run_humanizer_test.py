"""Quick CLI runner — humanizes test_ai_document.txt and saves result."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# Inline the pipeline without GUI
import random, re, nltk
from pathlib import Path
from humanizer_pro import HumanizerConfig, humanize_text, ensure_nltk_data

ensure_nltk_data()

src = Path(__file__).parent / "test_ai_document.txt"
raw = src.read_text(encoding="utf-8")

cfg = HumanizerConfig(
    swap_prob=0.22,
    min_word_len=6,
    subs_prob=0.98,
    phrase_prob=0.99,
    contract_prob=0.85,
    aside_prob=0.70,
    burst_split_prob=0.85,
    burst_merge_prob=0.55,
    fragment_prob=0.18,
    imperf_rate=0.15,
    rare_syn_bias=0.35,
    min_syn_freq=2,
    double_pass=False,
    use_gemini=True,
    rng=random.Random(42),
)

result = humanize_text(raw, cfg)

out = Path(__file__).parent / "test_ai_document_humanized.txt"
out.write_text(result, encoding="utf-8")

print("=== ORIGINAL ===")
print(raw[:600])
print("\n=== HUMANIZED ===")
print(result[:600])
print(f"\nSaved to: {out}")
