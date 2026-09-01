# -*- coding: utf-8 -*-
"""
QuillBot-style constrained paraphraser, safe for technical/scientific text.

Principles that make it safe (the opposite of humanizer_pro.py):
  * Sentence-by-sentence, length-locked  -> can never bloat or duplicate.
  * Protects numbers, units, chemical formulas, citations, and a glossary of
    exact technical terms by masking them before paraphrase and restoring them
    verbatim after -> no term corruption ("current density" stays exact).
  * Verifies every sentence (placeholders intact, length in bounds, still one
    sentence, no duplicated clause). If a sentence fails, the ORIGINAL is kept.
  * Capable model only (Cerebras llama-3.3-70b), no tiny local fallback.

CLI:  python paraphrase_safe.py in.docx out.docx
"""
import os
import re
import sys
import json
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

KEYS_ENV = r"C:\Users\chkam\OneDrive\Desktop\BrandFinder\.keys.env"

# --- provider pool (primary first) ------------------------------------------
_GEM = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
PROVIDERS = [
    # gemini-2.5 models have their own free-tier quota buckets (separate from the
    # 2.0/flash-latest ones), so these stay alive when the others are exhausted.
    ("GEMINI_API_KEY", _GEM, "gemini-2.5-flash-lite"),
    ("GEMINI_API_KEY", _GEM, "gemini-2.5-flash"),
    ("SAMBANOVA_API_KEY", "https://api.sambanova.ai/v1/chat/completions",
     "Meta-Llama-3.3-70B-Instruct"),
    ("GEMINI_API_KEY", _GEM, "gemini-flash-latest"),
]

# SambaNova free tier is requests-per-minute limited. Enforce a global minimum
# spacing between calls so we pace under the limit instead of tripping 429s.
import threading
import time as _time
_THROTTLE = threading.Lock()
_LAST = [0.0]
MIN_INTERVAL = 4.5  # seconds between outbound requests (stay under ~15 RPM)


def _pace():
    with _THROTTLE:
        wait = MIN_INTERVAL - (_time.time() - _LAST[0])
        if wait > 0:
            _time.sleep(wait)
        _LAST[0] = _time.time()

# Exact multi-word technical terms that must NOT be reworded (synonym-swapping
# these changes the science). Longest first so nested terms mask correctly.
GLOSSARY = sorted([
    "oxygen reduction reaction", "oxygen evolution reaction", "round-trip efficiency",
    "coulombic efficiency", "current density", "power density", "energy density",
    "specific capacity", "open-circuit voltage", "gas diffusion layer",
    "triple-phase boundary", "air cathode", "zinc anode", "bifunctional catalyst",
    "reporting standard", "cycle life",
], key=len, reverse=True)

# Tokens carrying quantitative / chemical meaning -> protect.
GLOSSARY_RE = re.compile("|".join(re.escape(t) for t in GLOSSARY), re.IGNORECASE)
CITATION = re.compile(r"\[[0-9][0-9,\s\-\u2013]*\]")
# any token containing a digit (numbers, units, formulas w/ subscripts, charges)
NUMISH = re.compile(r"[A-Za-z0-9()\u00b7]*\d[A-Za-z0-9()\u00b7+\-\u2212/.%^]*")
# formula-like all-caps clusters without digits: ZnO, KOH, NaOH, ORR, OER, DFT
FORMULA = re.compile(r"\b(?:[A-Z][a-z]?){2,}\b")
# bare ions
ION = re.compile(r"\bOH[\-\u2212]|\bH\+|\be[\-\u2212]")
PROTECT_PATTERNS = [GLOSSARY_RE, CITATION, NUMISH, ION, FORMULA]

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
ABBR = ("et al.", "e.g.", "i.e.", "vs.", "Fig.", "Eq.", "Ref.", "cf.", "approx.")


def load_keys():
    if not os.path.exists(KEYS_ENV):
        return
    with open(KEYS_ENV, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _post(url, key, model, prompt, temperature):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": 500,
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": _UA,
    })
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()


def call_llm(prompt, temperature=0.7):
    """Try each provider; on rate-limit (429) or 5xx, back off and retry a few
    times before moving to the next provider."""
    import time
    last = None
    for key_env, url, model in PROVIDERS:
        key = os.environ.get(key_env)
        if not key:
            continue
        for attempt in range(4):
            _pace()
            try:
                return _post(url, key, model, prompt, temperature)
            except urllib.error.HTTPError as e:
                last = f"{key_env}:{e.code}"
                if e.code in (429, 500, 502, 503, 529):
                    time.sleep(6 * (attempt + 1))  # 6,12,18,24s backoff
                    continue
                break  # non-retryable -> next provider
            except Exception as e:
                last = f"{key_env}:{e}"
                time.sleep(2.0)
                continue
    raise RuntimeError(f"All providers failed. Last: {last}")


def protect(text):
    """Single non-overlapping pass: collect every protected span across all
    patterns over the ORIGINAL text, merge overlaps, then replace right-to-left.
    This prevents a later pattern from matching inside a placeholder already
    inserted by an earlier one (the cascade bug)."""
    spans = []
    for pat in PROTECT_PATTERNS:
        for m in pat.finditer(text):
            if m.end() > m.start():
                spans.append((m.start(), m.end()))
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    mapping = {}
    out = text
    for i, (s, e) in enumerate(sorted(merged, reverse=True)):
        key = f"[[K{len(merged) - 1 - i}]]"
        mapping[key] = text[s:e]
        out = out[:s] + key + out[e:]
    return out, mapping


def restore(text, mapping):
    for key, tok in mapping.items():
        text = text.replace(key, tok)
    return text


def split_sentences(text):
    # guard abbreviations by temporarily hiding the period
    safe = text
    for a in ABBR:
        safe = safe.replace(a, a.replace(".", "\x00"))
    parts = SENT_SPLIT.split(safe)
    return [p.replace("\x00", ".") for p in parts if p.strip()]


def _one_sentence(s):
    inner = re.sub(r"[.!?]+$", "", s.strip())
    return not re.search(r"[.!?]\s+[A-Z(]", inner)


def _clean_model_output(out, masked):
    """Strip conversational preamble / multiple options; pick the single best line."""
    out = out.strip()
    # drop code fences
    out = re.sub(r"^```[a-z]*\n?|\n?```$", "", out).strip()
    lines = [ln.strip().strip('"').strip() for ln in out.splitlines() if ln.strip()]
    if not lines:
        return ""
    want = set(re.findall(r"\[\[K\d+\]\]", masked))
    # prefer a line that contains all placeholders and looks like prose
    scored = []
    for ln in lines:
        if re.match(r"^(here|sure|certainly|option|rewritten|paraphrase|\d[.)])",
                    ln, re.IGNORECASE):
            continue
        got = set(re.findall(r"\[\[K\d+\]\]", ln))
        scored.append((len(want & got), len(ln), ln))
    if scored:
        scored.sort(reverse=True)
        return scored[0][2]
    return lines[-1]


DANGLING = re.compile(r"\b(and|or|but|the|a|an|of|to|in|on|with|for|by|as|"
                      r"that|which|is|are|was|were)\s*$", re.IGNORECASE)


def _verify(sentence, out, masked, mapping):
    """Return (restored, reason). reason 'ok' means it passed."""
    want = set(re.findall(r"\[\[K\d+\]\]", masked))
    got = set(re.findall(r"\[\[K\d+\]\]", out))
    if want != got:
        return None, "placeholder"
    restored = restore(out, mapping).strip()
    wi, wo = len(sentence.split()), len(restored.split())
    if wo > wi * 1.4 or wo < wi * 0.6:
        return None, "length"
    # must be a complete sentence: ends with terminal punctuation, no dangling word
    if not re.search(r"[.!?][\"'’)\]]?$", restored):
        return None, "noend"
    if DANGLING.search(re.sub(r"[.!?][\"'’)\]]?$", "", restored)):
        return None, "dangling"
    if not _one_sentence(restored):
        return None, "multi"
    words = restored.split()
    shingles = [" ".join(words[i:i + 8]) for i in range(len(words) - 7)]
    if len(shingles) != len(set(shingles)):
        return None, "dup"
    return restored, "ok"


def paraphrase_sentence(sentence):
    masked, mapping = protect(sentence)
    prompt = (
        "Rewrite the following single sentence using different words and structure, "
        "keeping the EXACT same meaning and roughly the same length. Rules:\n"
        "- Return ONE complete sentence ending in a period. Do not truncate.\n"
        "- Do not add, remove, or invent information.\n"
        "- Keep every token of the form [[K0]], [[K1]], ... EXACTLY, character for "
        "character; never translate, drop, renumber, or reorder them.\n"
        "- Do not repeat any clause. Output ONLY the rewritten sentence, no quotes, "
        "no preamble, no options.\n\n"
        f"Sentence: {masked}"
    )
    for temp in (0.7, 0.4):  # one retry at lower temperature
        try:
            out = _clean_model_output(call_llm(prompt, temp), masked)
        except Exception:
            return sentence, "api_fail"
        restored, reason = _verify(sentence, out, masked, mapping)
        if reason == "ok":
            return restored, "ok"
    return sentence, reason


def paraphrase_paragraph(text):
    sents = split_sentences(text)
    stats = {"ok": 0, "kept": 0}
    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(paraphrase_sentence, sents))
    out = []
    for (new, why) in results:
        out.append(new)
        stats["ok" if why == "ok" else "kept"] += 1
        if why not in ("ok",):
            stats.setdefault(why, 0)
            stats[why] += 1
    return " ".join(out), stats


REF_LINE = re.compile(r"^\s*\[\d+\]")
CAPTION_LINE = re.compile(r"^\s*(Figure|Fig\.|Table)\s*\d", re.IGNORECASE)


def is_heading(p):
    style = (p.style.name or "").lower()
    return "head" in style or "title" in style


def _skip_paragraph(p):
    """Never paraphrase headings, titles, short lines, reference entries, or
    figure/table captions — their exact wording/formatting must be preserved."""
    t = p.text.strip()
    if is_heading(p) or len(t.split()) < 12:
        return True
    if REF_LINE.match(t) or CAPTION_LINE.match(t):
        return True
    return False


def _render_bar(current: int, total: int, width: int = 24) -> None:
    if total <= 0:
        return
    pct = (current / total) * 100.0
    filled = int(width * (current / total))
    bar = "█" * filled + "░" * (width - filled)
    sys.stderr.write(f"\r  [{bar}] {pct:5.1f}% ({current}/{total} paras)")
    sys.stderr.flush()
    if current >= total:
        sys.stderr.write("\n")
        sys.stderr.flush()


def run_docx(src, dst):
    import docx
    d = docx.Document(src)
    agg = {"ok": 0, "kept": 0}
    samples = []
    paras_to_process = [p for p in d.paragraphs if not _skip_paragraph(p)]
    total = len(paras_to_process)

    for idx, p in enumerate(paras_to_process, 1):
        _render_bar(idx, total)
        t = p.text
        new, st = paraphrase_paragraph(t)
        for k, v in st.items():
            agg[k] = agg.get(k, 0) + v
        if new != t and p.runs:
            if len(samples) < 3:
                samples.append((t, new))
            p.runs[0].text = new
            for r in p.runs[1:]:
                r.text = ""

    d.save(dst)
    return agg, samples, total


def main():
    load_keys()
    if len(sys.argv) < 3:
        print("Usage: python paraphrase_safe.py <in.docx> <out.docx>")
        sys.exit(1)
    src, dst = sys.argv[1], sys.argv[2]
    agg, samples, total_p = run_docx(src, dst)

    ok = agg.get("ok", 0)
    kept = agg.get("kept", 0)
    total_sents = ok + kept
    succ_rate = (ok / max(1, total_sents)) * 100.0

    print("\n┌────────────────────────────────────────────────────────┐")
    print("│         SAFE PARAPHRASER — PROGRESS REPORT             │")
    print("├────────────────────────────────────────────────────────┤")
    print(f"│  Paragraphs Processed: {total_p:<31} │")
    print(f"│  Sentences Rewritten:  {ok:<31} │")
    print(f"│  Sentences Retained:   {kept:<31} │")
    print(f"│  Paraphrase Success:   {succ_rate:>5.1f}%{'':<25} │")
    print(f"│  Output Saved:         {str(dst):<31} │")
    print("└────────────────────────────────────────────────────────┘\n")

    if samples:
        print("Sample Transformations:")
        for a, b in samples:
            print("  • BEFORE:", a[:120].encode("ascii", "replace").decode())
            print("    AFTER: ", b[:120].encode("ascii", "replace").decode())


if __name__ == "__main__":
    main()
