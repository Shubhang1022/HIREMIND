"""
EmbeddingEncoder — wraps sentence-transformers for CPU-only batch encoding.

Designed to be importable even when sentence_transformers is NOT installed.
The model is loaded lazily: only when encode_batch() or encode_single() is
first called (or when load_model() is called explicitly).
"""

from __future__ import annotations

import numpy as np


_MODEL_CACHE: dict[str, object] = {}


class EmbeddingEncoder:
    """Wraps sentence-transformers for CPU-only batch encoding."""

    def __init__(self, model_name: str = "BAAI/bge-large-en-v1.5") -> None:
        self.model_name = model_name
        self._model = None  # lazy load

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_model(self) -> None:
        """Load model weights. Uses device='cpu'."""
        global _MODEL_CACHE
        if self.model_name in _MODEL_CACHE:
            self._model = _MODEL_CACHE[self.model_name]
            return

        # Lazy import so this module is importable without sentence_transformers.
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        model = SentenceTransformer(self.model_name, device="cpu")
        _MODEL_CACHE[self.model_name] = model
        self._model = model

    def encode_batch(
        self,
        texts: list[str],
        normalize: bool = True,
        *,
        bge_mode: str | None = None,
    ) -> np.ndarray:
        """
        Encode a batch of texts.

        Returns
        -------
        np.ndarray
            Shape ``[len(texts), embedding_dim]``, dtype ``float32``.

        Auto-loads the model if it has not been loaded yet.

        Notes
        -----
        If the selected model is a BGE v1.5 model (e.g. ``BAAI/bge-large-en-v1.5``),
        you can set ``bge_mode`` to:
          - ``"query"``   → prefixes each text with ``"query: "``
          - ``"passage"`` → prefixes each text with ``"passage: "``

        This follows the recommended prompting for BGE retrieval embeddings.
        """
        self._ensure_loaded()
        encoded_texts = self._apply_bge_prompt(texts, bge_mode=bge_mode)
        embeddings = self._model.encode(
            encoded_texts,
            normalize_embeddings=normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        # Guarantee float32 regardless of model default
        return embeddings.astype(np.float32)

    def encode_single(
        self,
        text: str,
        normalize: bool = True,
        *,
        bge_mode: str | None = None,
    ) -> np.ndarray:
        """
        Encode a single text.

        Returns
        -------
        np.ndarray
            Shape ``[embedding_dim]``.
        """
        result = self.encode_batch([text], normalize=normalize, bge_mode=bge_mode)
        return result[0]

    @property
    def embedding_dim(self) -> int:
        """Return the embedding dimension (384 for MiniLM-L6)."""
        self._ensure_loaded()
        return self._model.get_sentence_embedding_dimension()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load the model if it has not been loaded yet."""
        global _MODEL_CACHE
        if self.model_name in _MODEL_CACHE:
            self._model = _MODEL_CACHE[self.model_name]
            return
        if self._model is None:
            self.load_model()

    def _apply_bge_prompt(self, texts: list[str], *, bge_mode: str | None) -> list[str]:
        """Apply BGE v1.5 query/passage prefixes when requested.

        We only apply the prefix when:
          - ``bge_mode`` is provided, and
          - the configured model name looks like a BGE v1.5 model.
        """
        if not bge_mode:
            return texts

        model_lower = (self.model_name or "").lower()
        is_bge = "bge" in model_lower and "v1.5" in model_lower
        if not is_bge:
            return texts

        mode = bge_mode.strip().lower()
        if mode not in ("query", "passage"):
            return texts

        prefix = f"{mode}: "
        # Avoid double-prefixing if caller already prefixed.
        out: list[str] = []
        for t in texts:
            tt = t or ""
            if tt.startswith("query: ") or tt.startswith("passage: "):
                out.append(tt)
            else:
                out.append(prefix + tt)
        return out
