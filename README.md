# 🧠 Humanizer Pro

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://github.com/Kamran5H/Humanizer)
[![LLM Pool](https://img.shields.io/badge/LLM%20Pool-9%20Failover%20Providers-8B5CF6?style=for-the-badge)](PROVIDERS.md)
[![Detection Scorer](https://img.shields.io/badge/Local%20AI%20Scorer-Offline%20Heuristics-10B981?style=for-the-badge)](https://github.com/Kamran5H/Humanizer)
[![Format Support](https://img.shields.io/badge/Formats-.DOCX%20%7C%20Markdown%20%7C%20TXT-EC4899?style=for-the-badge&logo=microsoftword&logoColor=white)](https://github.com/Kamran5H/Humanizer)

**Enterprise-grade AI text naturalization pipeline with a 9-provider resilient failover pool, format-preserving DOCX parser, and local zero-latency AI detection scorer.**

[Features](#-core-features) • [Architecture](#-failover--pipeline-architecture) • [Provider Pool](#-9-tier-resilient-llm-pool) • [Quickstart](#-quick-start) • [Configuration](#-configuration) • [License](#-license)

</div>

---

## 🌟 Executive Overview

**Humanizer Pro** transforms mechanical, repetitive, and formulaic AI-generated text into authentic, fluent, and naturally paced human prose. Unlike cloud-dependent wrappers that hit strict daily rate-limits or compromise confidential documents, Humanizer Pro features:

1. **A 9-Tier Resilient Provider Pool**: Transparently fails over across Cerebras, SambaNova, OpenRouter, GitHub Models, Together, Mistral, DeepInfra, and local Ollama instances.
2. **Local AI-Detection Scorer**: Instantly calculates burstiness, perplexity variance, sentence rhythm divergence, and signature n-gram clusters locally without telemetry or API costs.
3. **Format-Preserving Document Engine**: Ingests `.docx` documents and rewrites content paragraph-by-paragraph while strictly preserving headings, font hierarchies, tables, bullet points, and inline styling.

---

## 🚀 Core Features

- **🔄 Multi-Provider Auto-Failover**: Never get blocked by rate limits. If Groq or Cerebras exhausts daily token quotas (TPD/RPM), the pipeline seamlessly routes the next paragraph to SambaNova or OpenRouter with zero downtime.
- **📊 Real-Time Detection Scoring**: Built-in evaluation harness based on perplexity variance, burstiness, syntax distribution, and vocabulary variety to verify text authenticity before export.
- **📄 Native `.docx` Document Processing**: Full round-trip parsing (`safe_humanize_docx.py`) ensures Microsoft Word documents maintain all table formatting, citations, and layout structures.
- **🧪 Stealth Evaluation Harness**: Automated test harness (`detector_harness.py`, `ai_checker_test.py`) capable of running batch benchmarks against synthetic AI corpora.
- **🖥️ GUI & CLI Interfaces**: Launch an intuitive desktop interface (`humanizer_pro.py`, `run_humanizer.vbs`) or automate batch document runs via headless Python commands.

---

## 🏗️ Failover & Pipeline Architecture

```mermaid
flowchart TD
    A[Input: Text or .docx Document] --> B[Paragraph & Sentence Segmenter]
    B --> C{Dynamic Provider Pool}
    
    subgraph Providers [Resilient Provider Router]
        C1[1. Groq Cloud]
        C2[2. Cerebras Llama 3.3 70B]
        C3[3. SambaNova Llama 3.3]
        C4[4. OpenRouter Free Tier]
        C5[5. GitHub Models PAT]
        C6[6. Local Ollama Fallback]
    end
    
    C -->|Try Active| C1
    C1 -.->|On Quota / Rate-Limit| C2
    C2 -.->|Failover| C3
    C3 -.->|Failover| C4
    C4 -.->|Failover| C5
    C5 -.->|Offline / Cold Fallback| C6
    
    Providers --> D[Naturalized Candidate Text]
    D --> E[Local Heuristic AI Scorer]
    E -->|Perplexity & Burstiness Check| F{Human Score >= Threshold?}
    F -- No -->|Refine / Perturb Syntax| C
    F -- Yes --> G[Format Rebuilder: XML / .docx / TXT]
    G --> H[(Polished Human Output Document)]
```

---

## ⚡ 9-Tier Resilient LLM Pool

Humanizer Pro auto-discovers configured provider keys in `.env` / `.keys.env` and manages load-balancing and cooldown periods automatically:

| Priority | Provider | Flagship Model | Speed | Daily Quota |
| :--- | :--- | :--- | :--- | :--- |
| **#1** | **Groq** | `llama-3.3-70b-versatile` | ~300 tok/s | 100k tokens / day |
| **#2** | **Cerebras** | `llama-3.3-70b` | ~2,000 tok/s | ~1M tokens / day |
| **#3** | **SambaNova** | `Meta-Llama-3.3-70B` | ~400 tok/s | High free tier |
| **#4** | **OpenRouter** | `deepseek-chat:free`, `qwen:free` | Dynamic | Free zero-credit tier |
| **#5** | **GitHub Models** | `gpt-4o-mini`, `Llama-3.3-70B` | ~100 tok/s | Free PAT tier |
| **#6** | **Together AI** | `Llama-3.1-70B-Turbo` | ~150 tok/s | Free starter tier |
| **#7** | **Mistral AI** | `mistral-small-latest` | ~120 tok/s | Free tier |
| **#8** | **DeepInfra** | `meta-llama/Llama-3.3-70B-Turbo`| ~140 tok/s | Starter balance |
| **#9** | **Local Ollama** | `llama3.3:latest` / `qwen2.5` | Local GPU/CPU | Unlimited (Air-gapped) |

*For setup instructions, see [PROVIDERS.md](PROVIDERS.md).*

---

## 📁 Repository Structure

```text
Humanizer/
├── humanizer_pro.py            # Primary desktop application & GUI launcher
├── safe_humanize_docx.py       # Format-preserving Word document pipeline
├── paraphrase_safe.py          # Core NLP rewriting & synonym perturbation logic
├── ai_detector.py              # Heuristic AI-detection & rhythm evaluation module
├── detector_harness.py         # Batch verification & benchmarking harness
├── nlp_enhance.py              # Lexical burstiness & sentence variety injector
├── PROVIDERS.md                # Multi-provider setup guide and model override docs
├── STEALTH_NOTES.md            # In-depth research notes on syntactic stealth
├── run_humanizer.vbs           # Windows silent launcher
├── .gitignore                  # Virtualenv and runtime file exclusions
└── LICENSE                     # Open-source MIT License
```

---

## ⚡ Quick Start

### 1. Installation
```bash
# Clone the repository
git clone https://github.com/Kamran5H/Humanizer.git
cd Humanizer

# Setup virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# Install required dependencies
pip install requests python-docx numpy rich
```

### 2. Configure API Keys
Create a `.keys.env` or `.env` file in the root directory:
```dotenv
# At minimum, add Cerebras or OpenRouter for free unlimited usage:
CEREBRAS_API_KEY=csk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 3. Run Humanizer Pro
```bash
# Launch the Interactive GUI
python humanizer_pro.py

# Or process a Word document from the CLI
python safe_humanize_docx.py input.docx --output humanized.docx
```

---

## 📜 License

This project is open-source and released under the [MIT License](LICENSE).  
Copyright (c) 2024-2026 **Kamran Ashraf**.
