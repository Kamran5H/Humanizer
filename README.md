# Humanizer Pro

<div align="center">

[![CI](https://github.com/Kamran5H/Humanizer/actions/workflows/ci.yml/badge.svg)](https://github.com/Kamran5H/Humanizer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Works offline](https://img.shields.io/badge/Works-offline-10B981)](#offline-and-privacy)

**Rewrite formulaic text into clearer prose, with a local writing-pattern index.**

The index is **not a probability or percentage of AI authorship**, and is not
calibrated against Turnitin. A result of 13/100 here does not contradict or predict
an 87% Turnitin report. No rewriting mode guarantees a detector outcome or perfect
semantic equivalence; review the revised text against the original.

Turnitin defines its percentage over qualifying prose. Its current
[integration guidance](https://helpcenter.turnitin.com/hc/en-us/articles/49302416618637-How-do-I-display-and-access-the-AI-Writing-score-in-a-custom-API-integration)
says the AI Writing score is not exposed directly through its Core API. Compare
against an authorized Turnitin report rather than presenting this local index
as a Turnitin score.

For Word input, use **Download → Word document (.docx)**. Each result has its own
download file, independent of later uploads and jobs. The original DOCX package's
non-document parts (styles, figures, headers, footers, embedded files and other
metadata) are copied unchanged; body text is updated while retaining paragraph
and run formatting. Browser jobs also rewrite headings, while reference lists,
fields, equations and other protected content stay intact. Changed text may reflow
across lines or pages. **PDF input is text extraction only; original PDF layout
is not retained.**

Editing a Word preview as plain text cannot safely update its Word formatting.
If you change the input preview, use **Restore Word input** or edit and re-upload
the original document; the app will not silently flatten or ignore your edits.
Editing the result in the plain-text editor has the same limitation.
TXT/Markdown downloads include those edits; a formatted DOCX download reports a
conflict rather than silently losing the edits or formatting. To edit the formatted
result, download it and use Word. **Refine** reuses the formatted Word package
when the result has not been manually edited.

Studio **AI** mode requires a configured provider and fails clearly if no safe AI
rewrite is available. **Offline** is an explicit alternative. See [provider setup](PROVIDERS.md)
for OpenAI, native Claude/Anthropic, and other supported APIs.

You can also configure **OmniRoute**, **FreeLLMAPI**, or explicitly enable
OVHcloud's anonymous free tier. These routes use the same provider rotation,
cooldowns, cancellation-aware pacing and error reporting. See [free and gateway setup](PROVIDERS.md#omniroute-and-freellmapi-gateways).

## Optional trained detection

The Studio's **Trained detector** runs the MIT-licensed
[Desklib detector](https://github.com/desklib/ai-text-detector), independently of
the writing-pattern rules. Install its optional dependencies and pinned model:

```bash
pip install -e '.[detection]'
python -m humanizer.neural_detector --download
```

For CPU-only PyTorch, install `torch` from the official CPU wheel index first:
`pip install torch --index-url https://download.pytorch.org/whl/cpu`.
The model download is about 1.7 GB. It runs locally, checks the whole input in
token windows, and does not silently truncate a long document to its first page.
The explicit check accepts 40+ words and up to 100,000 characters. Larger texts
must be split into sections. Model installation is explicit; page loads and
health checks never download model weights.

Its displayed value is a token-weighted classifier score, **not the percentage
of AI-written words**. Intermediate scores (20–80) are shown as inconclusive;
this display band is not a calibrated accuracy claim. This is an English-text
model and still needs evaluation on your own human, AI and edited examples.
For evaluation, [RAID](https://github.com/liamdugan/raid) provides a shared
benchmark; [Binoculars](https://github.com/ahans30/Binoculars) is another research
detector, but its default inference requires two Falcon-7B models. Neither
establishes equivalence to Turnitin.

[Quick start](#quick-start) · [Web studio](#web-studio) · [Command line](#command-line) · [Python API](#python-api) · [How it works](#how-it-works) · [Providers](PROVIDERS.md)

<img src="docs/screenshot.png" alt="Humanizer Pro studio: original text with highlighted writing patterns, rewritten text with tracked changes, and a local pattern index" width="900">

</div>

## What it does

- **Rewrites** AI-generated drafts so they read like a person wrote them: varied sentence rhythm, plain verbs, no stock phrases.
- **Explains** the result with a local writing-pattern index broken into signals you can inspect (rhythm, AI vocabulary, formulaic structure, uniformity) and highlights every offending phrase with a suggested fix.
- **Checks factual anchors.** Rewrites are checked for numeric changes, dropped citations, changed negation and malformed formatting tokens. These checks reduce errors but cannot prove that meaning is unchanged.
- **Keeps Word formatting.** Bold, italics, colours, fonts, superscript citations, hyperlinks, Zotero/Mendeley/EndNote citation fields, footnotes and images survive. Studio jobs can rewrite headings; reference lists are left alone.
- **AI or offline rewriting.** AI mode uses configured providers with automatic failover and reports failures. Select Offline to use the local rule engine without an API key. Legacy scripts may opt into offline fallback.
- **Two registers.** *Academic* keeps a formal tone and never touches domain terms (catalyst, spectrum, yield, significant, robust …). *General* allows contractions and plainer wording.

## Quick start

```bash
git clone https://github.com/Kamran5H/Humanizer.git
cd Humanizer
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python humanizer_pro.py                                 # opens the web studio in your browser
```

Optional: copy `.env.example` to `.env` and paste in any API key for LLM-quality rewrites, or run [`OmniRoute_Colab.ipynb`](OmniRoute_Colab.ipynb) in Google Colab to host an OmniRoute gateway with a Cloudflare tunnel (see [PROVIDERS.md](PROVIDERS.md)). One free key is enough.

On Windows you can double-click `run_humanizer.vbs` to start the studio silently (it also creates the **Humanizer Pro** desktop shortcut automatically), `Start Humanizer.bat` for a visible console window, or `Install Desktop Shortcut.vbs` to create or repair the desktop icon.

## Web studio

`python humanizer_pro.py` (or `humanizer --web`) starts the studio at <http://127.0.0.1:8000>.

| | |
|---|---|
| **Live analysis** | The score updates as you type. *Highlight* marks every AI pattern and machine-sounding sentence; click a pattern in the sidebar to jump to it. |
| **Humanize** | Choose register, strength (Light / Balanced / Max) and engine (AI / Offline), then press **Ctrl + Enter**. Progress is live and **Esc** cancels (the partial result is kept). |
| **Changes** | Word-level diff of exactly what was rewritten. |
| **Integrity check** | Reports issues found by basic checks; review the result for meaning and factual accuracy. |
| **Files** | Drop or open `.docx`, `.pdf`, `.txt` or `.md`. Word files are rewritten in place with their formatting and downloaded as `.docx`. |
| **Refine** | Moves the result back into the input for another pass. |

It works entirely offline (no external fonts or scripts), remembers your draft and settings, and has light and dark themes.

## Command line

```bash
pip install -e .                       # installs the `humanizer` command (or use: python -m humanizer.cli)

humanizer essay.docx                   # -> essay_humanized.docx, formatting preserved
humanizer notes.md -o clean.md         # Markdown structure (headings, lists, code) preserved
humanizer report.pdf --general         # PDF text -> report_humanized.txt, conversational register
cat draft.txt | humanizer --stdin --local > out.txt   # offline only: nothing leaves your machine
humanizer --analyze draft.txt          # score + list of AI patterns, no rewriting
humanizer --text "..." --json          # machine-readable result incl. integrity report
```

Useful flags: `--level 1|2|3`, `--academic` (default) / `--general`, `--local`, `--seed N` (reproducible offline output), `--workers N`, `--no-report`, `--gui`.

## Python API

```python
from humanizer import HumanizerConfig, humanize_text, humanize_docx, analyze

cfg = HumanizerConfig(scientific=True, stealth_level=3)   # use_gemini=False forces offline
print(humanize_text("Furthermore, it is important to note that ...", cfg))
print(cfg.engine_used)                                    # "llm", "rules" or "mixed"

humanize_docx("paper.docx", cfg, dst="paper_humanized.docx")

report = analyze(text)            # score, verdict, signals, tells (with offsets), sentence scores
```

## How it works

```mermaid
flowchart LR
    A[Text / DOCX / PDF / MD] --> B[Segment<br/>keep headings, lists, code,<br/>skip references]
    B --> C[Lock numbers, citations,<br/>URLs, equations, formatting]
    C --> D{API key?}
    D -- yes --> E[LLM pool<br/>persona prompt, best-of-N,<br/>re-roll robotic sentences]
    D -- no --> F[Offline rule engine]
    E -->|fails or unfaithful| F
    E --> G[Fidelity gate<br/>numbers, citations, tags, length, meaning]
    F --> G
    G --> H[Polish + grammar]
    H --> I[Rebuild formatting] --> J[Score & integrity report]
```

**Pattern catalogue.** `humanizer/patterns.py` is the single list of AI-writing tells used by the detector, the rewriter, the linter and the LLM prompt. It combines the project's own measured tells with the categories documented in Wikipedia's [*Signs of AI writing*](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing) guide and the open-source [blader/humanizer](https://github.com/blader/humanizer) skill:

| Category | Example | Fix |
|---|---|---|
| AI vocabulary | *delve, leverage, pivotal, tapestry, showcase* | plain word |
| Inflated significance | *plays a crucial role in, stands as a testament to* | state the fact |
| Copula avoidance | *serves as a, boasts* | *is, has* |
| Formulaic transitions | *Furthermore, Moreover, Ultimately,* | drop or vary |
| Shallow *-ing* tails | *…, highlighting the need for* | separate sentence |
| Negative parallelism | *not only X but also Y* | *X and Y* |
| Filler & hedging | *it is important to note that, could potentially* | delete |
| Chatbot residue | *I hope this helps, As of my last update* | delete |
| Em-dash overuse | *X — Y — Z* | commas |
| Generic conclusions | *The future looks bright.* | delete |

**Pattern index.** The local index blends sentence-length variation, weighted phrase density, structure and uniformity. The optional GPT-2 signal is used only when its dependencies and cached model weights are already available. The bundled samples are regression fixtures, not an independent accuracy benchmark. The separate trained detector above provides a learned model signal, also without a Turnitin-equivalence claim.

**LLM pipeline.** Each paragraph is rewritten with instructions to retain facts, qualifiers and source formatting tokens. At *Balanced* and *Max*, the lowest local pattern index among candidates that pass basic fidelity checks is selected; this does not establish authorship or external detector performance. *Max* can refine individual sentences. Model output is not passed through the offline substitution pipeline. Interactive AI mode reports a failure if no safe candidate is produced; legacy clients may explicitly allow offline fallback.

## Offline and privacy

- With no API key, or with *Offline* / `--local`, nothing leaves your machine.
- The studio binds to `127.0.0.1`, accepts only localhost `Host` headers and sends no CORS headers, so other websites cannot use it or your keys.
- A `GITHUB_TOKEN` already in your environment is **not** used to call GitHub Models. Set `GITHUB_MODELS_TOKEN` (or put `GITHUB_TOKEN` in your `.env`) to opt in.
- Uploads are deleted after 24 hours. Interrupted jobs resume from a local checkpoint that is removed when the job finishes.

## Project layout

```text
humanizer/
  patterns.py   AI-writing pattern catalogue (single source of truth)
  rules.py      offline rewriter: locking, substitutions, structure repairs, grammar
  engine.py     LLM + offline orchestration, fidelity gate, text & DOCX pipelines
  detector.py   explainable writing-pattern index
  neural_detector.py optional pinned trained classifier
  providers.py  multi-provider LLM pool with failover and cooldowns
  document.py   DOCX tagging/rebuild, PDF/TXT/MD extraction, checkpoints
  lint.py       integrity and grammar checks, readability
  text.py       sentence splitting, conjugation (no NLTK needed)
  web.py, templates/index.html   web studio
  cli.py, gui.py                 command line and desktop app
OmniRoute_Colab.ipynb            Google Colab OmniRoute gateway + Drive backup & tunnel
Start Humanizer.bat / run_humanizer.vbs   Windows launchers (visible console / silent)
Install Desktop Shortcut.vbs     Creates or repairs the desktop icon
tools/detector_harness.py        optional checks against third-party detectors
tests/                           offline pytest suite
```

## Development

```bash
pip install -r requirements-dev.txt
pytest            # fully offline; API keys in your environment are ignored
ruff check .
```

Optional extras: `pip install pypdf` (PDF import, included in `requirements.txt`), `pip install ftfy` (encoding repair), `pip install torch transformers` (perplexity signal).

## License

[MIT](LICENSE) © 2024–2026 Kamran Ashraf.

---

<div align="center">

<a href="https://github.com/Kamran5H">
  <img src="https://raw.githubusercontent.com/Kamran5H/Kamran5H/main/assets/kamran-developer-badge.png" width="85" height="85" style="border-radius: 50%; border: 2px solid #00F2FE; box-shadow: 0 4px 14px rgba(0,0,0,0.18);" alt="Developer: Kamran Ashraf" />
</a>

<br/>

### 👨‍💻 **Developer:** [Kamran Ashraf](https://github.com/Kamran5H)
*AI Systems Architect • Computational Physical Chemist • Automation Pioneer*

[![Developer Profile](https://img.shields.io/badge/GitHub-Kamran5H-181717?style=flat-square&logo=github&logoColor=white)](https://github.com/Kamran5H)
[![Developer Portfolio](https://img.shields.io/badge/Portfolio-Visit%20Website-00F2FE?style=flat-square&logo=vercel&logoColor=black)](https://kamranashraf.is-a.dev)
[![Contact Email](https://img.shields.io/badge/Email-chkamran32b%40gmail.com-EA4335?style=flat-square&logo=gmail&logoColor=white)](mailto:chkamran32b@gmail.com)

</div>
