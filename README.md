# Humanizer Pro

A desktop tool that rewrites AI-generated text to read naturally, with a **built-in local AI-detection scorer** so you can measure how "human" the output reads before you ship it.

## Features

- **Multi-provider LLM pool** — routes rewrites across several providers with automatic fallback
- **Local detection scorer** — scores text for AI-writing signals without a cloud round-trip
- **Document in / document out** — works on `.docx` and plain text
- **Stealth eval harness** — batch-evaluate rewrites across topics (see `stealth_eval/`)
- **Tkinter GUI** — `humanizer_pro.py`

## Run

```bash
pip install -r requirements.txt
python humanizer_pro.py
```

Configure provider API keys via environment variables (see `PROVIDERS.md`). No keys are bundled.

## Layout

- `humanizer_pro.py` — GUI entry point
- `humanizer/` — provider pool, rewrite + scoring engine
- `stealth_eval/` — evaluation harness and sample outputs
- `*_test.docx` / `*_test.txt` — test fixtures
