"""
Tests for EmbeddingEncoder.

All tests are skipped automatically when sentence_transformers is not installed.
"""

import numpy as np
import pytest

# Skip the entire module if sentence_transformers is not available.
pytest.importorskip("sentence_transformers")

from src.features.embedding import EmbeddingEncoder  # noqa: E402


@pytest.fixture(scope="module")
def encoder() -> EmbeddingEncoder:
    """Shared encoder instance — model loaded once per test session."""
    enc = EmbeddingEncoder()
    enc.load_model()
    return enc


# ---------------------------------------------------------------------------
# Shape tests
# ---------------------------------------------------------------------------


def test_encode_batch_shape(encoder: EmbeddingEncoder) -> None:
    """encode_batch(['hello', 'world']) should return shape (2, 1024)."""
    result = encoder.encode_batch(["hello", "world"])
    assert result.shape == (2, 1024), f"Expected (2, 1024), got {result.shape}"


def test_encode_single_shape(encoder: EmbeddingEncoder) -> None:
    """encode_single('hello') should return shape (1024,)."""
    result = encoder.encode_single("hello")
    assert result.shape == (1024,), f"Expected (1024,), got {result.shape}"


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
# Embedding dimension property
# ---------------------------------------------------------------------------


def test_embedding_dim_property(encoder: EmbeddingEncoder) -> None:
    """embedding_dim should equal 1024 for BAAI/bge-large-en-v1.5."""
    assert encoder.embedding_dim == 1024


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
