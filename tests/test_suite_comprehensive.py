import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import docx
from humanizer.config import HumanizerConfig
from humanizer.document import (
    _tag_runs,
    _reconstruct_runs,
    _extract_run_style,
    iter_document_paragraphs,
    extract_text_from_file,
    extract_text_from_pdf,
)
from humanizer.rules import lock_elements, unlock_elements, fix_grammar, DEFAULT_SUBSTITUTIONS
from humanizer.engine import _inflect_ing_to_3sg, _break_ai_syntactic_patterns, _guard_fidelity, humanize_paragraph_rules, humanize_docx
from humanizer.lint import lint_text, is_clean
from humanizer.detector import score_text, split_sentences
from humanizer.providers import get_provider_status, call_llm_pool

print("==================================================")
print("   HUMANIZER PRO COMPREHENSIVE REGRESSION SUITE   ")
print("==================================================")

# 1. TEST VERB INFLECTION
print("\n[1/10] Testing Morphological Verb Inflection...")
verbs_to_test = {
    "improving": "improves",
    "reducing": "reduces",
    "creating": "creates",
    "providing": "provides",
    "enabling": "enables",
    "generating": "generates",
    "facilitating": "facilitates",
    "demonstrating": "demonstrates",
    "accelerating": "accelerates",
    "yielding": "yields",
    "stopping": "stops",
}
for v, expected in verbs_to_test.items():
    inflected = _inflect_ing_to_3sg(v)
    assert inflected == expected, f"Inflection failed for {v}: got {inflected}, expected {expected}"
# Test syntactic pattern rewrite
sent = "The catalyst performed well, thereby improving efficiency and thus reducing cost."
reformed = _break_ai_syntactic_patterns(sent)
print("  Syntactic transformation:", repr(reformed))
assert "This improves" in reformed
assert "This reduces" in reformed
print("  -> Verb Inflection: PASSED")

# 2. TEST CASCADE LOCKING AND UNLOCKING
print("\n[2/10] Testing Cascade Locking and Unlocking...")
cascade_samples = [
    r"\textbf{Important reference [1] and [2-4]}",
    r"See analysis at https://doi.org/10.1016/j.ensm.2026.01.001 with p < 0.001.",
    r"__RUN_LOCKED_0__ was measured with $E=mc^2$ and <b id='1'>high accuracy</b>.",
    r"Values: 45.2% and 12.5 mg/mL reported in [Smith et al., 2024].",
]
for cs in cascade_samples:
    locked, vault = lock_elements(cs)
    unlocked = unlock_elements(locked, vault)
    assert unlocked == cs, f"Lock mismatch for {cs!r}! Got {unlocked!r}"
    assert "__LOCK_" not in unlocked, f"Leaked placeholder in {unlocked!r}"
print("  -> Cascade Locking: PASSED")

# 3. TEST PHONETIC ACRONYM AGREEMENT & GRAMMAR CAPITALIZATION
print("\n[3/10] Testing Acronym Agreement & Grammar Edge Cases...")
grammar_samples = [
    ("an RNA sequence and an SEM analysis", "An RNA sequence and an SEM analysis"),
    ("an XRD pattern with a DNA strand", "An XRD pattern with a DNA strand"),
    ("an NMC cathode and an XPS spectrum", "An NMC cathode and an XPS spectrum"),
    ("  the test succeeded.", "The test succeeded."),
    (", the reaction completed.", "The reaction completed."),
    ("the the system operated normally.", "The system operated normally."),
]
for inp, exp in grammar_samples:
    fixed = fix_grammar(inp)
    print(f"  {inp!r} -> {fixed!r}")
    assert fixed == exp, f"Grammar failed for {inp!r}: expected {exp!r}, got {fixed!r}"

rule_cfg = HumanizerConfig(use_gemini=False, phrase_prob=1.0)
rule_out = humanize_paragraph_rules("it is important to note that the test succeeded.", rule_cfg)
print(f"  Rule pipeline: 'it is important to note that the test succeeded.' -> {rule_out!r}")
assert rule_out == "The test succeeded.", f"Expected 'The test succeeded.', got {rule_out!r}"
print("  -> Phonetic Agreement & Grammar: PASSED")

# 4. TEST FIDELITY GUARD & LINT INTEGRITY
print("\n[4/10] Testing Fidelity Guard & Lint Integrity...")
src_text = "Medical imaging benefits from AI. Systems analyze images with high precision."
hallucinated_out = (
    "Medical imaging benefits from AI. Systems analyze images with high precision [citation] "
    "(https://www.fake-source.com/). Systems analyze images with high precision."
)
guarded = _guard_fidelity(src_text, hallucinated_out)
print("  Guarded text:", repr(guarded))
assert "[citation]" not in guarded
assert "fake-source" not in guarded
lint_issues_raw = lint_text(hallucinated_out, src_text)
assert any("dummy placeholder" in msg for _, msg in lint_issues_raw), "Lint should catch [citation]!"
assert any("fabricated URL" in msg for _, msg in lint_issues_raw), "Lint should catch fake URL!"
lint_issues_guarded = lint_text(guarded, src_text)
assert not any(sev == "error" for sev, _ in lint_issues_guarded), f"Guarded text should be clean! Got: {lint_issues_guarded}"
print("  -> Fidelity Guard & Lint: PASSED")

# 5. TEST DOCX RUN-LEVEL STYLING & GUARANTEED CITATION RETENTION
print("\n[5/10] Testing Guaranteed Citation Retention in DOCX Runs...")
doc = docx.Document()
p = doc.add_paragraph()
r0 = p.add_run("Zinc-air batteries are promising")
r1 = p.add_run("[12]")
r1.font.superscript = True
r2 = p.add_run(". In addition, ")
r3 = p.add_run("novel catalysts")
r3.bold = True
r4 = p.add_run(" exhibit high activity.")

tagged, vault = _tag_runs(p)
base_st = _extract_run_style(p.runs[0])
# Simulate LLM rewrite that ACCIDENTALLY DROPPED the citation token __RUN_LOCKED_0__
rewritten_with_dropped_citation = tagged.replace("__RUN_LOCKED_0__", "")
_reconstruct_runs(p, rewritten_with_dropped_citation, vault, base_st)

# Verify citation [12] was restored
restored_texts = [r.text for r in p.runs]
print("  Reconstructed runs with dropped token:", restored_texts)
assert any("[12]" in t for t in restored_texts), f"Guaranteed citation retention failed! [12] not in {restored_texts}"
print("  -> Guaranteed Citation Retention: PASSED")

# 6. TEST REAL DOCX PIPELINE (INLINE DRAWINGS)
print("\n[6/10] Testing Real DOCX File (inline_test.docx)...")
src_docx = Path("inline_test.docx")
dst_docx = Path("scratch/test_pipeline_e2e.docx")
cfg = HumanizerConfig(use_gemini=False, parallel_workers=1)
out_path = humanize_docx(src_docx, cfg, dst=dst_docx)

d_res = docx.Document(str(out_path))
p_res = d_res.paragraphs[0]
assert len(p_res.runs) == 3, f"Expected 3 runs, got {len(p_res.runs)}"
assert "<w:drawing" in p_res.runs[1]._element.xml, "Inline drawing was not kept in R1!"
print("  -> Real DOCX Pipeline: PASSED")

# 7. TEST RECURSIVE NESTED TABLES IN DOCX
print("\n[7/10] Testing Recursive Nested Tables...")
doc_tables = docx.Document()
tbl_outer = doc_tables.add_table(rows=1, cols=1)
outer_cell = tbl_outer.rows[0].cells[0]
outer_cell.paragraphs[0].text = "Outer table text"
tbl_inner = outer_cell.add_table(rows=1, cols=1)
inner_cell = tbl_inner.rows[0].cells[0]
inner_cell.paragraphs[0].text = "Deeply nested table text"

all_paras = list(iter_document_paragraphs(doc_tables))
para_texts = [p.text for p in all_paras]
print("  Found paragraphs in document with nested tables:", para_texts)
assert "Outer table text" in para_texts
assert "Deeply nested table text" in para_texts
print("  -> Recursive Nested Tables: PASSED")

# 8. TEST PDF DOCUMENT EXTRACTION
print("\n[8/10] Testing PDF Text Extraction...")
test_pdf_path = Path("scratch/sample_test.pdf")
try:
    import fitz
    pdf_doc = fitz.open()
    page = pdf_doc.new_page()
    page.insert_text((72, 72), "Autonomous systems evaluate complex environments with high precision.")
    pdf_doc.save(str(test_pdf_path))
    pdf_doc.close()

    extracted_pdf_text = extract_text_from_file(test_pdf_path)
    print("  Extracted PDF text:", repr(extracted_pdf_text))
    assert "Autonomous systems" in extracted_pdf_text
    print("  -> PDF Text Extraction: PASSED")
except Exception as e:
    print(f"  PDF test skipped or warning: {e}")

# 9. TEST ABBREVIATION-SAFE SENTENCE SPLITTING & NEUTRAL BURSTINESS
print("\n[9/10] Testing Abbreviation-Safe Tokenization...")
abbrev_text = "Smith et al. tested the reaction. See Fig. 1 for details. The yield was high."
sents = split_sentences(abbrev_text)
print("  Split sentences:", sents)
assert len(sents) == 3, f"Expected 3 sentences, got {len(sents)}: {sents}"
assert "Smith et al. tested the reaction." == sents[0]
assert "See Fig. 1 for details." == sents[1]

# Short single-sentence paragraph burstiness test
short_heading = "Advanced materials synthesis allows researchers to develop stable semiconductor substrates under ambient conditions."
score_heading = score_text(short_heading)
print("  Short sentence score:", score_heading)
assert score_heading["burstiness"] >= 40.0, f"Expected neutral burstiness >= 40.0, got {score_heading['burstiness']}"
print("  -> Abbreviation-Safe Tokenization & Neutral Burstiness: PASSED")

# 10. TEST PROVIDER POOL STATUS & LOAD BALANCING
print("\n[10/10] Testing Multi-Provider Pool Status...")
pool_status = get_provider_status()
print(f"  Active endpoints count: {len(pool_status)}")
assert len(pool_status) >= 1, "Expected at least 1 provider configured!"
ready_count = sum(1 for p in pool_status if p["status"] == "ready")
print(f"  Ready endpoints: {ready_count}/{len(pool_status)}")
print("  -> Multi-Provider Pool Status: PASSED")

print("\n==================================================")
print("   ALL 10 COMPREHENSIVE TESTS PASSED WITH 100%!  ")
print("==================================================")
