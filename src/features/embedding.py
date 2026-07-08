"""
EmbeddingEncoder — wraps sentence-transformers for CPU-only batch encoding.

The model is obtained EXCLUSIVELY via ``model_service.get_model()``.
This module NEVER instantiates SentenceTransformer directly.
There is exactly ONE place in the entire production codebase that calls
SentenceTransformer(): backend/app/services/model_service.py

Production default: BAAI/bge-small-en-v1.5 (384-dim, 90 MB).
"""

from __future__ import annotations

import logging
import numpy as np

logger = logging.getLogger(__name__)

# _MODEL_CACHE is kept for backwards compatibility — model_service still
# back-fills it after loading so that legacy code paths hitting _MODEL_CACHE
# directly get a cache-hit without a duplicate download.
_MODEL_CACHE: dict[str, object] = {}

# Production default — must stay in sync with:
#   backend/Dockerfile                →  SentenceTransformer('BAAI/bge-small-en-v1.5', ...)
#   backend/app/core/config.py        →  embedding_model = "BAAI/bge-small-en-v1.5"
#   backend/app/services/model_service.py  →  _DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_PRODUCTION_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class EmbeddingEncoder:
    """Thin wrapper around the model_service singleton.

    NEVER creates a SentenceTransformer itself.  All model loading is
    delegated to model_service.get_model() which enforces the process-wide
    singleton guarantee.
    """

    def __init__(self, model_name: str = _PRODUCTION_DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self._model = None  # injected by _get_encoder() or loaded via model_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_model(self) -> None:
        """Obtain the model from model_service singleton.

        Does NOT instantiate SentenceTransformer — that is model_service's job.
        Calling this directly is only needed by legacy code; the production path
        uses _get_encoder() which injects self._model before any encode call.
        """
        # Check the backwards-compat cache first
        if self.model_name in _MODEL_CACHE:
            self._model = _MODEL_CACHE[self.model_name]
            logger.debug("[EmbeddingEncoder] load_model: cache hit for %s", self.model_name)
            return

        # Delegate to the process-wide singleton — never creates a duplicate
        try:
            from app.services.model_service import get_model
            self._model = get_model()
            # Back-fill cache so future direct cache lookups hit
            _MODEL_CACHE[self.model_name] = self._model
            logger.debug("[EmbeddingEncoder] load_model: obtained from model_service for %s", self.model_name)
        except Exception as exc:
            # model_service not available (e.g. standalone CLI usage outside backend)
            # Fall back to direct load ONLY in that case — never in the production backend
            logger.warning(
                "[EmbeddingEncoder] model_service unavailable (%s) — "
                "falling back to direct load. This should ONLY happen in CLI/test contexts.",
                exc,
            )
            self._load_model_direct()

    def _load_model_direct(self) -> None:
        """Last-resort direct load for CLI/test contexts outside the FastAPI backend.

        This method exists solely so standalone scripts (precompute.py, rank.py)
        can still use EmbeddingEncoder without the full FastAPI app.
        It is NEVER called in the production Docker container.
        """
        import os
        import gc
        global _MODEL_CACHE

        # Set HF cache to match model_service defaults
        if not os.environ.get("HF_HOME"):
            os.environ["HF_HOME"] = "/app/.cache/huggingface"
        if not os.environ.get("TRANSFORMERS_CACHE"):
            os.environ["TRANSFORMERS_CACHE"] = "/app/.cache/huggingface"
        if not os.environ.get("SENTENCE_TRANSFORMERS_HOME"):
            os.environ["SENTENCE_TRANSFORMERS_HOME"] = "/app/.cache/sentence-transformers"

        # Keep only one model in cache at a time
        _MODEL_CACHE.clear()
        gc.collect()

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
        """Encode a batch of texts.

        Returns np.ndarray shape [len(texts), embedding_dim], dtype float32.
        """
        self._ensure_loaded()
        encoded_texts = self._apply_bge_prompt(texts, bge_mode=bge_mode)
        embeddings = self._model.encode(
            encoded_texts,
            normalize_embeddings=normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return embeddings.astype(np.float32)

    def encode_single(
        self,
        text: str,
        normalize: bool = True,
        *,
        bge_mode: str | None = None,
    ) -> np.ndarray:
        """Encode a single text. Returns np.ndarray shape [embedding_dim]."""
        return self.encode_batch([text], normalize=normalize, bge_mode=bge_mode)[0]

    @property
    def embedding_dim(self) -> int:
        """Embedding dimension, read dynamically from the loaded model."""
        self._ensure_loaded()
        return self._model.get_sentence_embedding_dimension()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Ensure the model is available, loading via model_service if needed."""
        if self.model_name in _MODEL_CACHE:
            self._model = _MODEL_CACHE[self.model_name]
            return
        if self._model is None:
            self.load_model()

    def _apply_bge_prompt(self, texts: list[str], *, bge_mode: str | None) -> list[str]:
        """Apply BGE v1.5 query/passage prefixes when requested."""
        if not bge_mode:
            return texts
        model_lower = (self.model_name or "").lower()
        if not ("bge" in model_lower and "v1.5" in model_lower):
            return texts
        mode = bge_mode.strip().lower()
        if mode not in ("query", "passage"):
            return texts
        prefix = f"{mode}: "
        out: list[str] = []
        for t in texts:
            tt = t or ""
            if tt.startswith("query: ") or tt.startswith("passage: "):
                out.append(tt)
            else:
                out.append(prefix + tt)
        return out
