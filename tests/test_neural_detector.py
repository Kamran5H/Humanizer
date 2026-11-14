from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from humanizer import neural_detector


def test_full_document_is_checked_without_silent_truncation(monkeypatch):
    seen = []

    class Tensor:
        def __init__(self, values):
            self.values = values

        def to(self, device):
            return self

    class Tokenizer:
        def encode(self, text, **kwargs):
            assert kwargs == {"add_special_tokens": False, "truncation": False}
            return list(range(10))

        def num_special_tokens_to_add(self, pair):
            return 2

        def prepare_for_model(self, chunk, **kwargs):
            seen.extend(chunk)
            return {"input_ids": Tensor(chunk), "attention_mask": Tensor([1] * len(chunk))}

    class Model:
        config = SimpleNamespace(max_position_embeddings=5)

        def __call__(self, input_ids, attention_mask):
            return 0.9 if input_ids.values[0] < 6 else 0.1

    torch = SimpleNamespace(inference_mode=nullcontext, sigmoid=lambda value: SimpleNamespace(item=lambda: value),
                            long="long", tensor=lambda values, dtype: values[0])
    monkeypatch.setattr(neural_detector, "_load_bundle", lambda: (torch, Model(), Tokenizer(), "cpu"))
    result = neural_detector.detect("English sample text. " * 20)
    assert seen == list(range(10))  # the final partial window is included exactly once
    assert result["tokens"] == 10 and len(result["windows"]) == 4
    assert result["model_score"] == 58.0  # token-weighted, not an unweighted window mean
    assert result["positive_windows"] == 2 and result["calibrated"] is False
    assert result["label"] == "Inconclusive model signal"
    assert "not the percentage" in result["note"]


def test_uninstalled_detector_gives_setup_instructions(tmp_path, monkeypatch):
    monkeypatch.setattr(neural_detector, "model_directory", lambda: tmp_path)
    monkeypatch.setattr(neural_detector, "_BUNDLE", None)
    assert not neural_detector.detector_status()["installed"]
    with pytest.raises(ValueError, match="detector is not installed"):
        neural_detector.detect("A sufficiently long English sample paragraph. " * 10)


@pytest.mark.parametrize("text,message", [("Too short.", "at least 40 words"), ("word " * 21000, "100,000 characters")])
def test_invalid_inputs_are_rejected_before_model_loading(text, message):
    with pytest.raises(ValueError, match=message):
        neural_detector.detect(text)
