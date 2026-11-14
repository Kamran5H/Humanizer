"""Optional trained English-text detector, separate from writing-pattern rules.

Architecture and checkpoint: https://github.com/desklib/ai-text-detector (MIT).
Model files are pinned and downloaded only by the explicit setup command.
Inference never executes code supplied by a model repository.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import threading
from pathlib import Path

MODEL_ID = "desklib/ai-text-detector-v1.01"
REVISION = "5fdea974cd4287c61674951ec78803aa274e2fb7"
_FILES = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
          "special_tokens_map.json", "added_tokens.json", "spm.model")
_LOCK = threading.Lock()
_BUNDLE = None
_BUNDLE_PATH = None
_NOTE = ("Mean trained-classifier score across token windows, not the percentage of words written by AI. "
         "English-text model; not calibrated against Turnitin. False positives and false negatives are possible.")


def model_directory() -> Path:
    from humanizer.providers import load_keys_if_needed

    load_keys_if_needed()
    explicit = os.environ.get("HUMANIZER_DETECTOR_DIR")
    if explicit:
        return Path(explicit).expanduser()
    base = (os.environ.get("HUMANIZER_HOME") or os.environ.get("LOCALAPPDATA")
            or os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return Path(base) / "humanizer_pro" / "models" / "desklib-v1.01"


def detector_status() -> dict:
    dependencies = all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers", "sentencepiece"))
    downloaded = all((model_directory() / name).is_file() for name in _FILES)
    return {"model": MODEL_ID, "revision": REVISION, "installed": dependencies and downloaded,
            "dependencies_installed": dependencies, "model_downloaded": downloaded,
            "setup": "pip install -e '.[detection]' && python -m humanizer.neural_detector --download",
            "note": _NOTE}


def download_model() -> Path:
    from huggingface_hub import snapshot_download

    path = model_directory()
    snapshot_download(MODEL_ID, revision=REVISION, local_dir=path, allow_patterns=list(_FILES))
    return path


def _load_bundle():
    global _BUNDLE, _BUNDLE_PATH
    path = model_directory()
    if _BUNDLE is not None and _BUNDLE_PATH == path:
        return _BUNDLE
    if not detector_status()["installed"]:
        raise ValueError("The trained detector is not installed. Run: pip install -e '.[detection]' then "
                         "python -m humanizer.neural_detector --download. The model download is about 1.7 GB.")
    import torch
    from transformers import AutoConfig, AutoModel, AutoTokenizer, PreTrainedModel

    config = AutoConfig.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    if config.model_type != "deberta-v2":
        raise ValueError("Expected the pinned Desklib DeBERTa checkpoint.")

    class MeanPoolClassifier(PreTrainedModel):
        def __init__(self, model_config):
            super().__init__(model_config)
            self.model = AutoModel.from_config(model_config, trust_remote_code=False)
            self.classifier = torch.nn.Linear(model_config.hidden_size, 1)
            self.post_init()

        def forward(self, input_ids, attention_mask):
            hidden = self.model(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
            mask = attention_mask.unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp_min(1)
            return self.classifier(pooled)

    model, info = MeanPoolClassifier.from_pretrained(
        path, config=config, local_files_only=True, use_safetensors=True, output_loading_info=True,
    )
    if any(info.get(key) for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")):
        raise ValueError("Detector checkpoint weights do not match the expected architecture.")
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        torch.set_num_threads(min(4, torch.get_num_threads()))
    model.to(device).eval()
    _BUNDLE = (torch, model, tokenizer, device)
    _BUNDLE_PATH = path
    return _BUNDLE


def aggregate_windows(windows: list[dict]) -> dict:
    total = sum(window["tokens"] for window in windows)
    if not total:
        raise ValueError("No text tokens to classify.")
    score = sum(window["probability"] * window["tokens"] for window in windows) / total
    label = ("Model flags AI-like text" if score >= 0.8 else
             "Model flags human-like text" if score <= 0.2 else "Inconclusive model signal")
    return {"model_score": round(score * 100, 1), "score_type": "mean_window_classifier_score",
            "label": label,
            "tokens": total, "windows": windows, "positive_windows": sum(w["probability"] >= 0.5 for w in windows),
            "model": MODEL_ID, "revision": REVISION, "calibrated": False, "note": _NOTE}


def detect(text: str) -> dict:
    text = (text or "").strip()
    if len(text.split()) < 40:
        raise ValueError("Use at least 40 words for the trained detector; short excerpts are unreliable.")
    if len(text) > 100_000:
        raise ValueError("The trained detector accepts at most 100,000 characters per request. Split this document into sections.")
    # One CPU/GPU inference at a time avoids concurrent model loads and memory spikes.
    with _LOCK:
        torch, model, tokenizer, device = _load_bundle()
        ids = tokenizer.encode(text, add_special_tokens=False, truncation=False)
        size = min(512, model.config.max_position_embeddings) - tokenizer.num_special_tokens_to_add(pair=False)
        windows = []
        with torch.inference_mode():
            for start in range(0, len(ids), size):
                chunk = ids[start:start + size]
                encoded = tokenizer.prepare_for_model(chunk, return_attention_mask=True)
                encoded = {key: torch.tensor([value], dtype=torch.long) for key, value in encoded.items()}
                logits = model(input_ids=encoded["input_ids"].to(device),
                               attention_mask=encoded["attention_mask"].to(device))
                windows.append({"start_token": start, "tokens": len(chunk),
                                "probability": float(torch.sigmoid(logits).item())})
        return aggregate_windows(windows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install or run the optional trained text detector")
    parser.add_argument("--download", action="store_true", help="Download the pinned model (about 1.7 GB)")
    parser.add_argument("--text", help="English text to classify")
    parser.add_argument("file", nargs="?", help="UTF-8 text file to classify")
    args = parser.parse_args()
    if args.download:
        print(download_model())
    elif args.text is not None or args.file:
        print(json.dumps(detect(args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")), indent=2))
    else:
        print(json.dumps(detector_status(), indent=2))


if __name__ == "__main__":
    main()
