import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import re
import docx
from humanizer.document import _tag_runs, _extract_run_style, _apply_run_style, RunStyle

def new_reconstruct_runs(para, rewritten_text: str, vault: dict[str, dict], base_style: RunStyle) -> None:
    p_elem = para._element

    # 1. Identify complex elements to detach and preserve
    complex_elems = {
        v["_elem"] for v in vault.values()
        if v.get("locked") and v.get("_elem") is not None
    }

    # 2. Detach all existing runs and complex elements from paragraph
    for r in list(para.runs):
        p_elem.remove(r._element)

    attached_elems = set()

    # 3. Tokenize rewritten_text into tokens:
    #   - Semantic tags: <(b|i|bi)\s+id=['"](\d+)['"]>(.*?)</\1(?:\s+id=['"]\2['\"])?>
    #   - Locked placeholders: __RUN_LOCKED_\d+__
    token_pattern = re.compile(
        r"(<(?P<tag>b|i|bi)\s+id=['\"](?P<id>\d+)['\"]>(?P<content>.*?)</(?P=tag)(?:\s+id=['\"](?P=id)['\"])?>)|"
        r"(?P<lock>__RUN_LOCKED_\d+__)",
        re.DOTALL | re.IGNORECASE
    )

    last_idx = 0
    for m in token_pattern.finditer(rewritten_text):
        start, end = m.span()
        if start > last_idx:
            chunk = rewritten_text[last_idx:start]
            # Strip any residual unclosed tags
            chunk = re.sub(r"</?(?:bi|b|i)\b[^>]*>", "", chunk)
            if chunk:
                r = para.add_run(chunk)
                _apply_run_style(r, base_style)

        if m.group("lock"):
            tok = m.group("lock")
            v = vault.get(tok, {})
            elem = v.get("_elem")
            if elem is not None:
                p_elem.append(elem)
                attached_elems.add(elem)
            else:
                txt = v.get("text", "")
                st = v.get("style", base_style)
                if txt:
                    r = para.add_run(txt)
                    _apply_run_style(r, st)
        else:
            tag_type = m.group("tag").lower()
            tag_id = m.group("id")
            tag_content = m.group("content")
            clean_content = re.sub(r"</?(?:bi|b|i)\b[^>]*>", "", tag_content)
            key = f"{tag_type}_{tag_id}"
            st = vault.get(key, {}).get("style", base_style)
            if clean_content:
                r = para.add_run(clean_content)
                _apply_run_style(r, st)

        last_idx = end

    if last_idx < len(rewritten_text):
        trailing = rewritten_text[last_idx:]
        trailing = re.sub(r"</?(?:bi|b|i)\b[^>]*>", "", trailing)
        if trailing:
            r = para.add_run(trailing)
            _apply_run_style(r, base_style)

    # Safety: if any complex element wasn't placed because model dropped placeholder, append it
    for elem in complex_elems:
        if elem not in attached_elems:
            p_elem.append(elem)

# Test 1: inline_test.docx
print("=== TESTING INLINE DRAWING ===")
d = docx.Document("inline_test.docx")
p = d.paragraphs[0]
tagged_text, vault = _tag_runs(p)
base_style = _extract_run_style(p.runs[0])
print("Tagged:", tagged_text)
rewritten = tagged_text.replace("Here is some text with an inline image:", "This sentence contains a preserved inline image:")
new_reconstruct_runs(p, rewritten, vault, base_style)
print("Reconstructed runs count:", len(p.runs))
for i, r in enumerate(p.runs):
    has_draw = "<w:drawing" in r._element.xml
    print(f"  R{i}: text={r.text!r}, has_drawing={has_draw}")
assert "<w:drawing" in p.runs[1]._element.xml, "Drawing must be at index 1 (inline)!"
assert p.runs[0].text.startswith("This sentence contains"), "R0 must be leading text!"
assert p.runs[2].text.startswith(" and some more text"), "R2 must be trailing text!"
print("TEST 1 PASSED!")

# Test 2: Superscript citation preservation
print("\n=== TESTING SUPERSCRIPT CITATION ===")
doc2 = docx.Document()
p2 = doc2.add_paragraph()
r1 = p2.add_run("Zinc-air batteries are promising")
r2 = p2.add_run("1,2")
r2.font.superscript = True
r3 = p2.add_run(". However, degradation occurs.")
tagged2, vault2 = _tag_runs(p2)
base2 = _extract_run_style(p2.runs[0])
rewritten2 = tagged2.replace("Zinc-air batteries are promising", "Aqueous zinc-air systems show promise")
new_reconstruct_runs(p2, rewritten2, vault2, base2)
print("Reconstructed runs count:", len(p2.runs))
for i, r in enumerate(p2.runs):
    print(f"  R{i}: text={r.text!r}, superscript={r.font.superscript if r.font else None}")
assert p2.runs[1].text == "1,2"
assert p2.runs[1].font.superscript is True, "Superscript was not preserved!"
print("TEST 2 PASSED!")
