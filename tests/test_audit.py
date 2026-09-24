import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from humanizer.rules import lock_elements, unlock_elements

t = r"\textbf{see [1]}"
l, v = lock_elements(t)
print("Original:", repr(t))
print("Locked:  ", repr(l))
print("Vault:   ", v)
u = unlock_elements(l, v)
print("Unlocked:", repr(u))
assert u == t, f"MISMATCH! Expected {t!r}, got {u!r}"
print("SUCCESS!")
