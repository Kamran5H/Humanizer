import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import docx
from humanizer.config import HumanizerConfig
from humanizer.engine import humanize_docx

src = Path("inline_test.docx")
dst = Path("scratch/test_pipeline_out.docx")

cfg = HumanizerConfig(
    use_gemini=False, # Use rules engine first to verify offline docx preservation
    parallel_workers=1,
)

saved = humanize_docx(src, cfg, dst=dst)
print("Saved to:", saved)

d = docx.Document(str(saved))
p = d.paragraphs[0]
print("Re-opened paragraph text:", repr(p.text))
print("Runs count:", len(p.runs))
for i, r in enumerate(p.runs):
    has_draw = "<w:drawing" in r._element.xml
    print(f"  R{i}: text={r.text!r}, has_drawing={has_draw}")

assert "<w:drawing" in p.runs[1]._element.xml, "Drawing must be in R1!"
print("SUCCESSFUL DOCX VALIDATION!")
