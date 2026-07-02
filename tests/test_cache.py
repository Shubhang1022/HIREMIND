"""
Tests for FeatureCache — all run with a temporary directory via pytest's tmp_path fixture.
No sentence_transformers dependency required.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.features.cache import FeatureCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cache(tmp_path) -> FeatureCache:
    """Create a fresh FeatureCache backed by a temp directory."""
    return FeatureCache(cache_dir=str(tmp_path / "feature_cache"))


# ---------------------------------------------------------------------------
# Embedding round-trip
# ---------------------------------------------------------------------------


def test_round_trip_embeddings(tmp_path) -> None:
    """save_embedding_batch then load_embedding_batch should return identical array."""
    cache = _make_cache(tmp_path)
    arr = np.random.default_rng(0).random((10, 384), dtype=float).astype(np.float32)

    cache.save_embedding_batch(0, arr)
    loaded = cache.load_embedding_batch(0)

    np.testing.assert_array_equal(arr, loaded)
    assert loaded.dtype == np.float32


# ---------------------------------------------------------------------------
# Structured features round-trip
# ---------------------------------------------------------------------------


def test_round_trip_structured(tmp_path) -> None:
    """save_structured_batch then load_structured_batch should return identical list of dicts."""
    cache = _make_cache(tmp_path)
    features = [
        {"candidate_id": "CAND_0000001", "years_exp": 5.0, "ai_ml_skill_count": 3},
        {"candidate_id": "CAND_0000002", "years_exp": 7.5, "ai_ml_skill_count": 6},
    ]

    cache.save_structured_batch(0, features)
    loaded = cache.load_structured_batch(0)

    assert loaded == features


# ---------------------------------------------------------------------------
# Meta JSON round-trip
# ---------------------------------------------------------------------------


def test_round_trip_meta(tmp_path) -> None:
    """save_meta then load_meta should return identical dict."""
    cache = _make_cache(tmp_path)
    meta = {
        "total_candidates": 100_000,
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "batch_size": 512,
    }

    cache.save_meta(meta)
    loaded = cache.load_meta()

    assert loaded == meta


# ---------------------------------------------------------------------------
# Meta missing → empty dict
# ---------------------------------------------------------------------------


def test_load_meta_missing_returns_empty(tmp_path) -> None:
    """load_meta on a cache with no meta.json should return {}."""
    cache = _make_cache(tmp_path)
    assert cache.load_meta() == {}


# ---------------------------------------------------------------------------
# JD embedding round-trip
# ---------------------------------------------------------------------------


def test_round_trip_jd_embedding(tmp_path) -> None:
    """save_jd_embedding then load_jd_embedding should return identical array."""
    cache = _make_cache(tmp_path)
    arr = np.random.default_rng(42).random((1, 384), dtype=float).astype(np.float32)

    cache.save_jd_embedding(arr)
    loaded = cache.load_jd_embedding()

    np.testing.assert_array_equal(arr, loaded)
    assert loaded.dtype == np.float32


# ---------------------------------------------------------------------------
# batch_ids
# ---------------------------------------------------------------------------


def test_batch_ids(tmp_path) -> None:
    """After saving batches 0, 1, 2, batch_ids() should return [0, 1, 2]."""
    cache = _make_cache(tmp_path)
    dummy = np.zeros((4, 384), dtype=np.float32)

    cache.save_embedding_batch(0, dummy)
    cache.save_embedding_batch(1, dummy)
    cache.save_embedding_batch(2, dummy)

    assert cache.batch_ids() == [0, 1, 2]


# ---------------------------------------------------------------------------
# Flags round-trip
# ---------------------------------------------------------------------------


def test_save_flags_and_load(tmp_path) -> None:
    """save_flags and load_flags should round-trip honeypot_flags and disqualifier_types."""
    cache = _make_cache(tmp_path)

    honeypot_flags = np.array([0, 1, 2, 0, 4], dtype=np.uint8)
    disqualifier_types = {
        "CAND_0000002": "honeypot",
        "CAND_0000003": "consulting_only",
    }

    cache.save_flags(honeypot_flags, disqualifier_types)
    loaded_flags, loaded_types = cache.load_flags()

    np.testing.assert_array_equal(honeypot_flags, loaded_flags)
    assert loaded_flags.dtype == np.uint8
    assert loaded_types == disqualifier_types
