import os
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from humanizer import providers  # noqa: E402
from humanizer.config import HumanizerConfig  # noqa: E402
from humanizer.document import Checkpoint  # noqa: E402

_PROVIDER_VARS = [env for env, *_ in providers._FREE_PROVIDER_REGISTRY] + [
    "GITHUB_TOKEN", "GITHUB_MODELS_TOKEN", "STEALTH_API_BASE", "STEALTH_API_KEY", "STEALTH_MODEL",
    "STEALTH_FALLBACK_BASE", "STEALTH_FALLBACK_MODEL", "STEALTH_FALLBACK_KEY",
    "OMNIROUTE_API_KEY", "OMNIROUTE_BASE_URL", "OMNIROUTE_MODELS",
    "FREELLMAPI_API_KEY", "FREELLMAPI_BASE_URL", "FREELLMAPI_MODELS",
    "HUMANIZER_ENABLE_OVH_ANONYMOUS", "OVH_MODELS",
]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No real API keys, no real state dir, no network: every test runs offline and isolated."""
    for var in _PROVIDER_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(providers, "_KEYS_LOADED", True)
    monkeypatch.setattr(providers, "_STATE_FILE", tmp_path / "provider_state.json")
    monkeypatch.setattr(providers, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(providers, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(Checkpoint, "DIR", tmp_path / "sessions")
    providers._FILE_KEYS.clear()
    providers._LAST_SUCCESS.clear()
    providers.reset_cooldowns()
    yield
    providers._FILE_KEYS.clear()
    providers.reset_cooldowns()


@pytest.fixture
def cfg():
    return HumanizerConfig(use_gemini=False, scientific=True, rng=random.Random(7))


@pytest.fixture
def gcfg():
    return HumanizerConfig(use_gemini=False, scientific=False, rng=random.Random(7))


AI_TEXT = (
    "Artificial intelligence has emerged as a transformative force in healthcare, revolutionizing the way "
    "medical professionals diagnose, treat, and manage patient care. Furthermore, the integration of AI "
    "technologies has demonstrated remarkable potential in enhancing clinical outcomes, optimizing operational "
    "efficiency, and reducing overall costs. Moreover, machine learning algorithms can analyze vast amounts of "
    "medical data, thereby enabling practitioners to detect patterns that would otherwise remain hidden. "
    "It is important to note that these tools play a pivotal role in modern medicine, underscoring the need "
    "for robust governance. Additionally, stakeholders must foster collaboration to navigate the complexities "
    "of this ever-evolving landscape. Ultimately, the future looks bright."
)

HUMAN_TEXT = (
    "We ran the titration three times because the first two endpoints disagreed by almost half a millilitre. "
    "That was annoying. On the third run I swapped burettes, recalibrated the pH meter, and the numbers finally "
    "lined up. Our best guess is that the old burette had a slow leak near the stopcock. Nobody noticed it until "
    "Priya pointed out the wet bench. Lesson learned."
)

os.environ.setdefault("PYTHONHASHSEED", "0")
