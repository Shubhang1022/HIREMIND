"""
Tests for EmbeddingEncoder.

All tests are skipped automatically when sentence_transformers is not installed.
Dimension assertions are dynamic — they read ``encoder.embedding_dim`` and
``model.get_sentence_embedding_dimension()`` rather than hardcoding 1024.
Current production model: BAAI/bge-small-en-v1.5  (384-dim, 90 MB).
"""

import numpy as np
import pytest

# Skip the entire module if sentence_transformers is not available.
pytest.importorskip("sentence_transformers")

from src.features.embedding import EmbeddingEncoder  # noqa: E402


@pytest.fixture(scope="module")
def encoder() -> EmbeddingEncoder:
    """Shared encoder instance — model loaded once per test session.

    Uses the production default (BAAI/bge-small-en-v1.5) so tests always
    validate the model that will run on Render.
    """
    enc = EmbeddingEncoder()          # uses _PRODUCTION_DEFAULT_MODEL
    enc.load_model()
    return enc


# ---------------------------------------------------------------------------
# Shape tests — dynamic, not hardcoded to any dimension
# ---------------------------------------------------------------------------


def test_encode_batch_shape(encoder: EmbeddingEncoder) -> None:
    """encode_batch returns shape (n_texts, embedding_dim) for the loaded model."""
    result = encoder.encode_batch(["hello", "world"])
    expected_dim = encoder.embedding_dim
    assert result.shape == (2, expected_dim), (
        f"Expected shape (2, {expected_dim}), got {result.shape}. "
        f"Model: {encoder.model_name}"
    )


def test_encode_single_shape(encoder: EmbeddingEncoder) -> None:
    """encode_single returns a 1-D array of length embedding_dim."""
    result = encoder.encode_single("hello")
    expected_dim = encoder.embedding_dim
    assert result.shape == (expected_dim,), (
        f"Expected shape ({expected_dim},), got {result.shape}. "
        f"Model: {encoder.model_name}"
    )


# ---------------------------------------------------------------------------
# Normalisation test
# ---------------------------------------------------------------------------


def test_normalized_norms(encoder: EmbeddingEncoder) -> None:
    """With normalize=True every row's L2-norm should be ≈ 1.0 (within 1e-5)."""
    result = encoder.encode_batch(
        ["sentence one", "sentence two", "sentence three"], normalize=True
    )
    norms = np.linalg.norm(result, axis=1)
    np.testing.assert_allclose(
        norms,
        np.ones(len(norms)),
        atol=1e-5,
        err_msg=f"Not all norms are ≈ 1.0. Got: {norms}",
    )


# ---------------------------------------------------------------------------
# Embedding dimension property — dynamic
# ---------------------------------------------------------------------------


def test_embedding_dim_property(encoder: EmbeddingEncoder) -> None:
    """embedding_dim property matches model.get_sentence_embedding_dimension()."""
    # Read the true dim directly from the underlying model
    true_dim = encoder._model.get_sentence_embedding_dimension()
    assert encoder.embedding_dim == true_dim, (
        f"embedding_dim property ({encoder.embedding_dim}) does not match "
        f"model.get_sentence_embedding_dimension() ({true_dim})"
    )


def test_embedding_dim_is_positive(encoder: EmbeddingEncoder) -> None:
    """embedding_dim must be a positive integer."""
    assert isinstance(encoder.embedding_dim, int)
    assert encoder.embedding_dim > 0, f"Expected positive dim, got {encoder.embedding_dim}"


def test_production_model_dimension(encoder: EmbeddingEncoder) -> None:
    """Production model BAAI/bge-small-en-v1.5 must produce 384-dim vectors.

    This test is the single source of truth for the expected dimension.
    If this test fails, the Dockerfile and config.py must be re-checked.
    """
    if encoder.model_name != "BAAI/bge-small-en-v1.5":
        pytest.skip(
            f"Skipping production-dimension check: model is '{encoder.model_name}', "
            "not the production default 'BAAI/bge-small-en-v1.5'."
        )
    assert encoder.embedding_dim == 384, (
        f"BAAI/bge-small-en-v1.5 must produce 384-dim vectors, "
        f"got {encoder.embedding_dim}."
    )


# ---------------------------------------------------------------------------
# Semantic similarity tests
# ---------------------------------------------------------------------------


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two 1-D vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def test_similar_sentences_high_cosine(encoder: EmbeddingEncoder) -> None:
    """Semantically similar sentences should have cosine similarity > 0.5."""
    a = encoder.encode_single(
        "I work with machine learning and neural networks", normalize=True
    )
    b = encoder.encode_single(
        "Expert in deep learning and AI models", normalize=True
    )
    sim = _cosine_similarity(a, b)
    assert sim > 0.5, f"Expected cosine similarity > 0.5 for similar sentences, got {sim:.4f}"


def test_dissimilar_sentences_low_cosine(encoder: EmbeddingEncoder) -> None:
    """
    Semantically dissimilar sentences should have cosine similarity < 0.9,
    i.e. they are meaningfully different and not near-identical.
    """
    a = encoder.encode_single("machine learning engineer", normalize=True)
    b = encoder.encode_single("accountant at a bank", normalize=True)
    sim = _cosine_similarity(a, b)
    assert sim < 0.9, (
        f"Expected cosine similarity < 0.9 for dissimilar sentences, got {sim:.4f}. "
        "The embeddings are unexpectedly similar."
    )
