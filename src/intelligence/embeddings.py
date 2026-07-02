from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.features.embedding import EmbeddingEncoder


@dataclass
class BGELargeEmbeddingLayer:
    """Embedding layer using `BAAI/bge-large-en-v1.5`.

    Produces:
    - candidate embeddings (passage mode)
    - job embeddings (query mode)
    """

    model_name: str = "BAAI/bge-large-en-v1.5"

    def __post_init__(self) -> None:
        self._encoder = EmbeddingEncoder(model_name=self.model_name)

    def load(self) -> None:
        self._encoder.load_model()

    @property
    def dim(self) -> int:
        return int(self._encoder.embedding_dim)

    def embed_candidates(self, texts: list[str]) -> np.ndarray:
        return self._encoder.encode_batch(texts, normalize=True, bge_mode="passage")

    def embed_jobs(self, texts: list[str]) -> np.ndarray:
        return self._encoder.encode_batch(texts, normalize=True, bge_mode="query")

    def embed_job(self, text: str) -> np.ndarray:
        return self._encoder.encode_single(text, normalize=True, bge_mode="query")

