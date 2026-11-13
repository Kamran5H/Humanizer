"""Backwards-compatibility shim — the helpers now live in :mod:`humanizer.nlp`."""

from humanizer.nlp import capabilities, fix_encoding, is_faithful, meaning_similarity  # noqa: F401

if __name__ == "__main__":
    print("capabilities:", capabilities())
