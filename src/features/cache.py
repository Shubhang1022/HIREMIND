"""
FeatureCache — read/write feature cache to disk.

Stores:
  * embeddings (.npy)        — float32 arrays per batch
  * structured features (.pkl) — list[dict] per batch
  * flags (.npy / .pkl)      — honeypot flags and disqualifier types
  * jd embedding (.npy)      — single JD embedding
  * meta.json                — run metadata at the cache root
  * scores/                  — reserved for dimension_scores.npy (written later)

Subdirectory layout created on construction:
    <cache_dir>/
    ├── embeddings/
    ├── structured/
    ├── flags/
    ├── jd/
    └── scores/
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np


class FeatureCache:
    """Read/write feature cache to disk."""

    # ------------------------------------------------------------------
    # Construction / directory setup
    # ------------------------------------------------------------------

    def __init__(self, cache_dir: str = "./feature_cache") -> None:
        self.cache_dir = Path(cache_dir)
        # Create all required subdirectories
        for sub in ("embeddings", "structured", "flags", "jd", "scores"):
            (self.cache_dir / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Embedding batches
    # ------------------------------------------------------------------

    def save_embedding_batch(self, batch_id: int, arr: np.ndarray) -> None:
        """Save ``[B, 384]`` float32 array to ``embeddings/batch_{batch_id:03d}.npy``."""
        path = self.cache_dir / "embeddings" / f"batch_{batch_id:03d}.npy"
        np.save(str(path), arr.astype(np.float32))

    def load_embedding_batch(self, batch_id: int) -> np.ndarray:
        """Load ``embeddings/batch_{batch_id:03d}.npy`` → ``np.ndarray``."""
        path = self.cache_dir / "embeddings" / f"batch_{batch_id:03d}.npy"
        return np.load(str(path))

    # ------------------------------------------------------------------
    # Structured feature batches
    # ------------------------------------------------------------------

    def save_structured_batch(self, batch_id: int, features: list[dict]) -> None:
        """Save list of feature dicts to ``structured/batch_{batch_id:03d}.pkl``."""
        path = self.cache_dir / "structured" / f"batch_{batch_id:03d}.pkl"
        with open(path, "wb") as fh:
            pickle.dump(features, fh, protocol=pickle.HIGHEST_PROTOCOL)

    def load_structured_batch(self, batch_id: int) -> list[dict]:
        """Load ``structured/batch_{batch_id:03d}.pkl`` → ``list[dict]``."""
        path = self.cache_dir / "structured" / f"batch_{batch_id:03d}.pkl"
        with open(path, "rb") as fh:
            return pickle.load(fh)  # noqa: S301

    # ------------------------------------------------------------------
    # Meta JSON
    # ------------------------------------------------------------------

    def save_meta(self, meta: dict) -> None:
        """Save ``meta.json`` to cache root."""
        path = self.cache_dir / "meta.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, ensure_ascii=False)

    def load_meta(self) -> dict:
        """Load ``meta.json`` from cache root. Returns ``{}`` if not found."""
        path = self.cache_dir / "meta.json"
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    # ------------------------------------------------------------------
    # JD embedding
    # ------------------------------------------------------------------

    def save_jd_embedding(self, arr: np.ndarray) -> None:
        """Save ``[1, 384]`` float32 array to ``jd/jd_embedding.npy``."""
        path = self.cache_dir / "jd" / "jd_embedding.npy"
        np.save(str(path), arr.astype(np.float32))

    def load_jd_embedding(self) -> np.ndarray:
        """Load ``jd/jd_embedding.npy`` → ``np.ndarray``."""
        path = self.cache_dir / "jd" / "jd_embedding.npy"
        return np.load(str(path))

    # ------------------------------------------------------------------
    # Flags
    # ------------------------------------------------------------------

    def save_flags(
        self,
        honeypot_flags: np.ndarray,
        disqualifier_types: dict,
    ) -> None:
        """
        Save ``flags/honeypot_flags.npy`` and ``flags/disqualifier_types.pkl``.

        Parameters
        ----------
        honeypot_flags:
            1-D uint8 array; values 0=clean, 1=honeypot, 2=consulting disqualified,
            3=non-technical disqualified, 4=suspicion.
        disqualifier_types:
            ``dict[candidate_id → disqualifier_reason]``.
        """
        flags_dir = self.cache_dir / "flags"
        np.save(str(flags_dir / "honeypot_flags.npy"), honeypot_flags.astype(np.uint8))
        with open(flags_dir / "disqualifier_types.pkl", "wb") as fh:
            pickle.dump(disqualifier_types, fh, protocol=pickle.HIGHEST_PROTOCOL)

    def load_flags(self) -> tuple[np.ndarray, dict]:
        """
        Load and return ``(honeypot_flags array, disqualifier_types dict)``.
        """
        flags_dir = self.cache_dir / "flags"
        honeypot_flags = np.load(str(flags_dir / "honeypot_flags.npy"))
        with open(flags_dir / "disqualifier_types.pkl", "rb") as fh:
            disqualifier_types = pickle.load(fh)  # noqa: S301
        return honeypot_flags, disqualifier_types

    # ------------------------------------------------------------------
    # Batch ID discovery
    # ------------------------------------------------------------------

    def batch_ids(self) -> list[int]:
        """Return sorted list of batch IDs saved to ``embeddings/``."""
        embeddings_dir = self.cache_dir / "embeddings"
        ids: list[int] = []
        for p in embeddings_dir.glob("batch_*.npy"):
            # Filename pattern: batch_000.npy
            try:
                batch_id = int(p.stem.split("_")[1])
                ids.append(batch_id)
            except (IndexError, ValueError):
                pass
        return sorted(ids)

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Delete all files in the cache directory (for testing/reset)."""
        for p in self.cache_dir.rglob("*"):
            if p.is_file():
                p.unlink()
