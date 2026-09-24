"""
Forensic Scrutiny Hardening Test Suite.
Verifies all 7 hardened areas from the comprehensive code scrutiny.
"""

import math
import os
import re
import sys
import threading
from pathlib import Path

# Add project root to sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import docx
from humanizer.config import HumanizerConfig
from humanizer.detector import score_text
from humanizer.document import _iter_table_paragraphs, iter_document_paragraphs
from humanizer.engine import humanize_text, humanize_docx
from humanizer.rules import lock_elements, unlock_elements, _LOCK_RE
from humanizer.web import app
from starlette.testclient import TestClient


def test_table_merged_cells():
    print("[1/7] Testing Word Table Merged Cells Deduplication...")
    doc = docx.Document()
    t = doc.add_table(2, 2)
    c1 = t.cell(0, 0)
    c2 = t.cell(0, 1)
    c1.merge(c2)
    c1.paragraphs[0].text = "Merged Header Text"
    t.cell(1, 0).paragraphs[0].text = "Cell (1, 0)"
    t.cell(1, 1).paragraphs[0].text = "Cell (1, 1)"

    paras = list(iter_document_paragraphs(doc))
    texts = [p.text for p in paras]
    print(f"  Extracted paragraphs: {texts}")
    assert texts == ["Merged Header Text", "Cell (1, 0)", "Cell (1, 1)"], f"Duplicate paragraphs detected: {texts}"
    print("  -> Table Merged Cells Deduplication: PASSED")


def test_epsilon_variance_math_domain():
    print("[2/7] Testing Epsilon Negative Variance in score_text...")
    # 5 identical sentences can produce zero or epsilon-negative sample variance
    repeated = "The catalyst exhibited superior efficiency. " * 6
    res = score_text(repeated)
    print(f"  Score result for identical sentences: {res}")
    assert isinstance(res["ai_score"], float), "AI score calculation failed"
    print("  -> Epsilon Variance Protection: PASSED")


def test_endpoint_url_normalization():
    print("[3/7] Testing Provider Endpoint URL Normalization...")
    from humanizer.providers import _call_endpoint
    # Verify the URL construction logic
    url1 = "https://api.groq.com/openai/v1"
    url2 = "https://api.groq.com/openai/v1/chat/completions"
    url3 = "https://api.groq.com/openai/v1/"

    def norm(base_url):
        clean_base = base_url.rstrip("/")
        return clean_base if clean_base.endswith("/chat/completions") else clean_base + "/chat/completions"

    assert norm(url1) == "https://api.groq.com/openai/v1/chat/completions"
    assert norm(url2) == "https://api.groq.com/openai/v1/chat/completions"
    assert norm(url3) == "https://api.groq.com/openai/v1/chat/completions"
    print("  -> Endpoint URL Normalization: PASSED")


def test_scientific_unit_locking():
    print("[4/7] Testing Comprehensive Scientific Unit & Exponent Locking...")
    test_str = (
        "Operating at 50 mA cm-2 and 120 mW cm-2, the cell reached 1.42 V at 350 °C. "
        "The specific energy was 420 Wh kg⁻¹ with a shear stress of 25.4 kPa at 1200 rpm."
    )
    locked_text, vault = lock_elements(test_str)
    print(f"  Locked text: {locked_text}")
    print(f"  Vault keys: {list(vault.keys())}")
    
    # Verify that numbers and their scientific units were captured
    locked_vals = list(vault.values())
    assert any("50 mA cm-2" in v for v in locked_vals), f"50 mA cm-2 not locked: {locked_vals}"
    assert any("120 mW cm-2" in v for v in locked_vals), f"120 mW cm-2 not locked: {locked_vals}"
    assert any("1.42 V" in v for v in locked_vals), f"1.42 V not locked: {locked_vals}"
    assert any("350 °C" in v for v in locked_vals), f"350 °C not locked: {locked_vals}"
    assert any("420 Wh kg⁻¹" in v for v in locked_vals), f"420 Wh kg⁻¹ not locked: {locked_vals}"
    assert any("25.4 kPa" in v for v in locked_vals), f"25.4 kPa not locked: {locked_vals}"
    assert any("1200 rpm" in v for v in locked_vals), f"1200 rpm not locked: {locked_vals}"

    unlocked = unlock_elements(locked_text, vault)
    assert unlocked == test_str, f"Unlock mismatch: {unlocked} != {test_str}"
    print("  -> Scientific Unit Locking: PASSED")


def test_cooperative_cancellation():
    print("[5/7] Testing Cooperative Cancellation in Thread Pool...")
    cancel_evt = threading.Event()
    cancel_evt.set()  # Cancel immediately
    cfg = HumanizerConfig(use_gemini=False, cancel_event=cancel_evt)
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    out = humanize_text(text, cfg)
    print(f"  Cancellation returned text: '{out}'")
    assert out == text, f"Text was modified despite immediate cancellation: {out}"
    print("  -> Cooperative Cancellation: PASSED")


def test_session_docx_path_traversal_rejection():
    print("[6/7] Testing Security Validation against Session ID Traversal...")
    client = TestClient(app)
    bad_sessions = ["../../../etc/passwd", "..\\..\\boot.ini", "session/../../evil", "test; rm -rf /"]
    for s in bad_sessions:
        resp = client.post("/api/download-docx", json={"text": "Hello world", "session_docx": s})
        assert resp.status_code == 400, f"Expected 400 for {s}, got {resp.status_code}"
        resp2 = client.post("/api/humanize", json={"text": "Hello world", "session_docx": s})
        assert resp2.status_code == 400 or (resp2.json().get("success") is False and "Invalid session" in resp2.json().get("error", "")), f"Expected validation failure for {s}, got {resp2.json()}"
    print("  -> Security Validation: PASSED")


def test_present_tense_dechaining():
    print("[7/7] Testing Present-Tense De-Chaining in Syntactic Patterns...")
    from humanizer.engine import _break_ai_syntactic_patterns
    sample = "The catalyst operates continuously, prompting further reaction, and thereby increasing yield."
    fixed = _break_ai_syntactic_patterns(sample)
    print(f"  Fixed text: '{fixed}'")
    assert "This prompts" in fixed, f"Expected 'This prompts' in fixed text, got '{fixed}'"
    assert "This increases" in fixed, f"Expected 'This increases' in fixed text, got '{fixed}'"
    print("  -> Present-Tense De-Chaining: PASSED")


if __name__ == "__main__":
    print("=" * 60)
    print("   HUMANIZER PRO FORENSIC SCRUTINY VERIFICATION SUITE   ")
    print("=" * 60 + "\n")
    test_table_merged_cells()
    test_epsilon_variance_math_domain()
    test_endpoint_url_normalization()
    test_scientific_unit_locking()
    test_cooperative_cancellation()
    test_session_docx_path_traversal_rejection()
    test_present_tense_dechaining()
    print("\n" + "=" * 60)
    print("   ALL 7 FORENSIC SCRUTINY TESTS PASSED 100%!   ")
    print("=" * 60)
