import re

_LOCK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"__RUN_(?:LOCKED|PLACEHOLDER)_\d+__"), "PLACEHOLDER"),
    (re.compile(r"</?(?:bi|b|i)(?:\s+id=['\"]\d+['\"])?>", re.IGNORECASE), "TAG"),
    (re.compile(r"\[[a-zA-Z0-9,\-\s]{1,15}\]"), "CITATION"),
    (re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ]+"), "SUP_SUB"),
    (re.compile(r"\([A-Z][^()]{1,60}(?:19|20)\d{2}[^()]{0,30}\)"), "CITATION"),
    (re.compile(r"https?://\S+"), "URL"),
    (re.compile(r"www\.\S+"), "URL"),
    (re.compile(r"doi:\s*10\.\S+", re.IGNORECASE), "DOI"),
    (re.compile(r"\b(?:p|n|r|r²|R²|β|α|df|F|t|χ²|OR|RR|HR)\s*[=<>≤≥]\s*[\d.]+"), "STAT"),
    (re.compile(r"\b\d[\d,.]*\s*(?:%|mg|kg|ml|mL|μg|μL|mmol|cm|mm|nm|kb|Mb|GB|TB)\b"), "STAT"),
    (re.compile(r"\$[^$]+\$"), "EQUATION"),
    (re.compile(r"\\[a-zA-Z]+\{[^}]*\}"), "LATEX"),
]

def new_lock_elements(text: str) -> tuple[str, dict[str, str]]:
    vault: dict[str, str] = {}
    if not text:
        return text, vault

    # 1. Collect all candidate spans across all patterns on the ORIGINAL text
    spans: list[tuple[int, int, str]] = []
    for pat, label in _LOCK_PATTERNS:
        for m in pat.finditer(text):
            if m.end() > m.start():
                spans.append((m.start(), m.end(), label))

    if not spans:
        return text, vault

    # 2. Sort spans by start asc, length desc (longest match first)
    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))

    # 3. Resolve overlapping spans: keep the outer/earlier span
    merged: list[tuple[int, int, str]] = []
    for s, e, lbl in spans:
        if not merged:
            merged.append((s, e, lbl))
        else:
            prev_s, prev_e, prev_lbl = merged[-1]
            if s >= prev_e:
                # Disjoint span
                merged.append((s, e, lbl))
            else:
                # Overlap: if this span extends further than previous, merge or expand
                if e > prev_e:
                    merged[-1] = (prev_s, e, prev_lbl)

    # 4. Replace right-to-left so string indices remain stable
    counter = 0
    out = text
    # Reverse order for substitution
    for s, e, lbl in reversed(merged):
        tok = f"__LOCK_{lbl}_{counter}__"
        counter += 1
        vault[tok] = text[s:e]
        out = out[:s] + tok + out[e:]

    return out, vault

def new_unlock_elements(text: str, vault: dict[str, str]) -> str:
    if not text or not vault:
        return text
    # Multi-pass loop ensures any nested tokens expand cleanly to a fixed point
    for _ in range(5):
        changed = False
        for tok, orig in vault.items():
            if tok in text:
                text = text.replace(tok, orig)
                changed = True
        if not changed:
            break
    return text

# Test cases
test_cases = [
    r"\textbf{see [1]}",
    r"Studies show high efficiency (95.4%) [1-3] at https://doi.org/10.1016/j.ensm.2026.01.",
    r"The reaction $E=mc^2$ was observed with p < 0.05.",
    r"__RUN_LOCKED_0__ has <b id='1'>high</b> capacity.",
    r"Check out www.nature.com/articles/s41586-026-0001 for data [Smith et al., 2024].",
]

for tc in test_cases:
    locked, vault = new_lock_elements(tc)
    print("\nOriginal:", repr(tc))
    print("Locked:  ", repr(locked))
    print("Vault:   ", vault)
    unlocked = new_unlock_elements(locked, vault)
    print("Unlocked:", repr(unlocked))
    assert unlocked == tc, f"FAILED on {tc!r}! Got {unlocked!r}"
    # Verify no leaked tokens
    assert "__LOCK_" not in unlocked, f"LEAKED TOKEN in {unlocked!r}"

print("\nALL LOCK/UNLOCK TESTS PASSED PERFECTLY!")
