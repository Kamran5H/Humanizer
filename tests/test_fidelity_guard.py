import re

def clean_guard(src: str, out: str) -> str:
    # 1. Strip dummy hallucinated markers like [citation], [ref]
    out = re.sub(r"\[(?:citation|ref|reference|source|cite|insert\s+citation)\]", "", out, flags=re.IGNORECASE)

    # 2. Bracketed citations fidelity
    src_cites = set(re.findall(r"\[[a-zA-Z0-9,\-\s]{1,20}\]", src))
    out_cites = re.findall(r"\[[a-zA-Z0-9,\-\s]{1,20}\]", out)
    for c in out_cites:
        # Don't strip run placeholders or valid source citations
        if not re.match(r"^__(?:RUN|LOCK)_", c) and c not in src_cites:
            out = out.replace(c, "")

    # 3. URL fidelity: if src has no URLs, reject fabricated URLs
    src_urls = set(re.findall(r"https?://\S+|www\.\S+", src))
    if not src_urls:
        out = re.sub(r"\(?\b(?:https?://|www\.)[^\s()]+(?:\([^\s()]+\)[^\s()]*)*\)?", "", out)

    # Clean double spaces and punctuation spacing before sentence splitting
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out).strip()

    # 4. Remove consecutive duplicated sentences
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", out) if s.strip()]
    deduped = []
    for s in sents:
        if not deduped or s.lower() != deduped[-1].lower():
            deduped.append(s)
    out = " ".join(deduped)

    # Clean whitespace and stray punctuation
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()

src = "Medical imaging benefits from AI-powered diagnostic tools. Systems analyze images with high precision."
out = "Medical imaging benefits from AI. Systems exceed radiologists in cancer detection [citation] (https://www.nih.gov/). Systems exceed radiologists in cancer detection."

cleaned = clean_guard(src, out)
print("ORIGINAL:", repr(src))
print("CLEANED: ", repr(cleaned))
assert "[citation]" not in cleaned
assert "https://www.nih.gov" not in cleaned
assert cleaned.count("Systems exceed radiologists in cancer detection") == 1
print("GUARD TEST PASSED!")
