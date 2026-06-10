"""Pluggable embedding backends.

Default is a deterministic hashed character-trigram embedder with zero
dependencies, so the repo runs with one pip install. Set
CLAIMTRACE_EMBEDDER=sentence-transformers (and install the embeddings extra)
to use a local transformer model instead.
"""

from __future__ import annotations

import math
import os
import re
import zlib

DIM = 512
_TOKEN_RE = re.compile(r"[a-z0-9$%.]+")


class HashedTrigramEmbedder:
    """Stable bag-of-trigrams embedding. crc32 keeps the hash deterministic
    across processes, unlike Python's salted builtin hash."""

    dim = DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * DIM
        tokens = _TOKEN_RE.findall(text.lower())
        for tok in tokens:
            padded = f"^{tok}$"
            grams = [padded[i:i + 3] for i in range(len(padded) - 2)] or [padded]
            for g in grams:
                idx = zlib.crc32(g.encode("utf-8")) % DIM
                vec[idx] += 1.0
            idx = zlib.crc32(tok.encode("utf-8")) % DIM
            vec[idx] += 2.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._model.encode(texts, normalize_embeddings=True)]


def get_embedder():
    if os.environ.get("CLAIMTRACE_EMBEDDER") == "sentence-transformers":
        return SentenceTransformerEmbedder()
    return HashedTrigramEmbedder()


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))
