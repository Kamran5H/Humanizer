import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from humanizer.providers import call_llm_pool

try:
    resp = call_llm_pool("Say 'Antigravity test OK' and nothing else.")
    print("RESPONSE:", repr(resp))
except Exception as e:
    print("ERROR:", type(e), e)
