import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from humanizer.providers import _build_pool, _OAI_COOLDOWNS, reset_cooldowns
import time

reset_cooldowns()
p = _build_pool()
now = time.time()
print(f"Total endpoints in pool: {len(p)}")
for i, e in enumerate(p):
    cd = _OAI_COOLDOWNS.get(f"{e['base_url']}||{e['model']}", 0.0)
    wait = max(0.0, cd - now)
    print(f"  [{i+1:02d}] {e['name']:<14} | {e['model']:<42} | cooldown={wait:.1f}s")
