"""
Grammar, Readability, and Integrity Linter — Backwards Compatibility Bridge.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from humanizer.lint import lint_text, is_clean, readability

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    text = Path(args[1]).read_text(encoding="utf-8") if args[0] == "--file" else args[0]
    r = readability(text)
    print(f"readability: Flesch {r['flesch']}  avg-sent {r['avg_sentence_len']}w  burstiness {r['burstiness']}")
    issues = lint_text(text)
    if not issues:
        print("clean — no issues")
        sys.exit(0)
    for sev, msg in issues:
        print(f"  [{sev}] {msg}")
    sys.exit(1 if any(s == "error" for s, _ in issues) else 0)
