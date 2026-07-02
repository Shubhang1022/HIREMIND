"""Tests for SubmissionWriter and select_top_n.

Covers:
    test_valid_csv                — write a valid 100-row CSV, validate() returns []
    test_wrong_row_count          — write 99-row CSV, validate() returns row-count error
    test_non_monotonic_scores     — scores going up → validate() returns monotonicity error
    test_duplicate_ranks          — duplicate rank → validate() returns error
    test_candidate_id_format      — bad candidate_id → validate() returns error
    test_reasoning_too_long       — reasoning > 300 chars → validate() returns error
    test_write_creates_file       — write() creates the file at the specified path
    test_select_top_n_correct_count — select_top_n returns exactly N items
    test_select_top_n_ranked_correctly — items sorted by descending score, ranks 1..N
"""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

# Ensure project root is on sys.path so src.* imports work.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.output.writer import SubmissionWriter
from src.ranking.selector import select_top_n


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

def _make_valid_rows(n: int = 100) -> list[tuple[str, int, float, str]]:
    """Build a list of (candidate_id, rank, score, reasoning) with monotonically
    decreasing scores and valid candidate IDs."""
    rows = []
    for i in range(n):
        cand_id = f"CAND_{(i + 1):07d}"
        rank = i + 1
        score = 1.0 - i * 0.005          # 1.0, 0.995, 0.990, ...
        reasoning = f"Candidate {i + 1}: strong AI skills; Pune; 30d notice."
        rows.append((cand_id, rank, score, reasoning))
    return rows


@dataclass
class _FakeDimScores:
    """Minimal stand-in for DimScores in selector tests."""
    specialization_match: float = 0.5
    required_skills_match: float = 0.5
    relevant_experience: float = 0.5
    semantic_similarity: float = 0.5
    career_growth: float = 0.5
    behavioral_fit: float = 0.5
    integrity: float = 0.5
    education: float = 0.5
    disqualifier_multiplier: float = 1.0

    def final_score(self, weights=None):
        return 0.5


# ---------------------------------------------------------------------------
# SubmissionWriter tests
# ---------------------------------------------------------------------------

class TestSubmissionWriter:

    def test_write_creates_file(self, tmp_path):
        """write() must create the output file."""
        out = tmp_path / "submission.csv"
        writer = SubmissionWriter()
        writer.write(_make_valid_rows(), str(out))
        assert out.exists()

    def test_valid_csv(self, tmp_path):
        """A properly written 100-row CSV should pass validate() with no errors."""
        out = tmp_path / "submission.csv"
        writer = SubmissionWriter()
        writer.write(_make_valid_rows(), str(out))
        errors = writer.validate(str(out))
        assert errors == [], f"Unexpected validation errors: {errors}"

    def test_wrong_row_count(self, tmp_path):
        """A 99-row CSV must produce a row-count validation error."""
        out = tmp_path / "submission.csv"
        writer = SubmissionWriter()
        writer.write(_make_valid_rows(n=99), str(out))
        errors = writer.validate(str(out))
        assert any("100" in e for e in errors), (
            f"Expected row-count error, got: {errors}"
        )

    def test_non_monotonic_scores(self, tmp_path):
        """A CSV whose scores increase with rank must trigger a monotonicity error."""
        rows = _make_valid_rows()
        # Reverse scores so rank 1 has lowest score — clearly non-monotonic
        rows_bad = [
            (cid, rank, 0.01 * rank, reasoning)   # score *increases* with rank
            for cid, rank, _, reasoning in rows
        ]
        out = tmp_path / "submission.csv"
        writer = SubmissionWriter()
        writer.write(rows_bad, str(out))
        errors = writer.validate(str(out))
        assert any("monoton" in e.lower() for e in errors), (
            f"Expected monotonicity error, got: {errors}"
        )

    def test_duplicate_ranks(self, tmp_path):
        """A CSV with two rows sharing rank 1 must produce a duplicate-rank error."""
        rows = _make_valid_rows()
        # Replace rank of row 1 (rank 2) with rank 1 → duplicate
        rows[1] = (rows[1][0], 1, rows[1][2], rows[1][3])
        out = tmp_path / "submission.csv"
        writer = SubmissionWriter()
        writer.write(rows, str(out))
        errors = writer.validate(str(out))
        assert any("duplicate" in e.lower() or "rank" in e.lower() for e in errors), (
            f"Expected duplicate-rank error, got: {errors}"
        )

    def test_candidate_id_format(self, tmp_path):
        """A bad candidate_id (wrong format) must trigger a format validation error."""
        rows = _make_valid_rows()
        # Replace first row's candidate_id with an invalid one
        rows[0] = ("INVALID_ID", rows[0][1], rows[0][2], rows[0][3])
        out = tmp_path / "submission.csv"
        writer = SubmissionWriter()
        writer.write(rows, str(out))
        errors = writer.validate(str(out))
        assert any("candidate_id" in e.lower() or "invalid" in e.lower() for e in errors), (
            f"Expected candidate_id format error, got: {errors}"
        )

    def test_reasoning_too_long(self, tmp_path):
        """A reasoning string > 300 chars must trigger a length validation error."""
        rows = _make_valid_rows()
        long_reasoning = "A" * 301
        rows[0] = (rows[0][0], rows[0][1], rows[0][2], long_reasoning)
        out = tmp_path / "submission.csv"
        writer = SubmissionWriter()
        writer.write(rows, str(out))
        errors = writer.validate(str(out))
        assert any("reasoning" in e.lower() or "300" in e or "chars" in e.lower() for e in errors), (
            f"Expected reasoning length error, got: {errors}"
        )

    def test_score_format_four_decimal_places(self, tmp_path):
        """Scores in the written CSV must be formatted to 4 decimal places."""
        rows = _make_valid_rows(n=100)
        out = tmp_path / "submission.csv"
        writer = SubmissionWriter()
        writer.write(rows, str(out))

        # Read back and check score format
        with open(out, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                score_str = row["score"]
                assert "." in score_str, f"Score '{score_str}' has no decimal point"
                decimal_part = score_str.split(".")[1]
                assert len(decimal_part) == 4, (
                    f"Score '{score_str}' does not have exactly 4 decimal places"
                )
                break  # Just check the first row

    def test_file_missing_returns_error(self, tmp_path):
        """validate() on a non-existent file must return a 'not found' error."""
        out = tmp_path / "missing.csv"
        writer = SubmissionWriter()
        errors = writer.validate(str(out))
        assert len(errors) == 1
        assert "not found" in errors[0].lower() or "missing" in str(out).lower()

    def test_rank_one_score_zero_fails_validation(self, tmp_path):
        """A CSV where Rank #1 has a score of 0.0 must trigger a validation error."""
        rows = _make_valid_rows()
        # Set Rank #1 score to 0.0
        rows[0] = (rows[0][0], rows[0][1], 0.0, rows[0][3])
        out = tmp_path / "submission.csv"
        writer = SubmissionWriter()
        writer.write(rows, str(out))
        errors = writer.validate(str(out))
        assert any("Rank #1 score is 0.0" in e or "match percentage is 0" in e for e in errors)

    def test_all_weak_matches_fails_validation(self, tmp_path):
        """A CSV where all candidate scores are Weak Match (e.g. < 0.40) must fail validation."""
        rows = _make_valid_rows()
        # Scale all scores so they are all <= 0.39 (Weak Match range)
        rows_weak = [
            (cid, rank, 0.39 - rank * 0.001, reasoning)
            for cid, rank, _, reasoning in rows
        ]
        out = tmp_path / "submission.csv"
        writer = SubmissionWriter()
        writer.write(rows_weak, str(out))
        errors = writer.validate(str(out))
        assert any("Weak Match" in e for e in errors)


# ---------------------------------------------------------------------------
# select_top_n tests
# ---------------------------------------------------------------------------

class TestSelectTopN:

    def _make_candidates(self, n: int):
        """Generate n candidates with linearly increasing scores (0.01..n*0.01)."""
        ids = [f"CAND_{(i + 1):07d}" for i in range(n)]
        scores = np.array([0.01 * (i + 1) for i in range(n)], dtype=np.float32)
        dims = [_FakeDimScores() for _ in range(n)]
        return ids, scores, dims

    def test_select_top_n_correct_count(self):
        """select_top_n must return exactly N items when N ≤ len(candidates)."""
        ids, scores, dims = self._make_candidates(200)
        result = select_top_n(ids, scores, dims, n=100)
        assert len(result) == 100

    def test_select_top_n_fewer_than_n(self):
        """select_top_n must return all items when len(candidates) < N."""
        ids, scores, dims = self._make_candidates(50)
        result = select_top_n(ids, scores, dims, n=100)
        assert len(result) == 50

    def test_select_top_n_ranked_correctly(self):
        """Items must be sorted descending by score, with ranks 1..N ascending."""
        ids, scores, dims = self._make_candidates(200)
        result = select_top_n(ids, scores, dims, n=10)

        assert len(result) == 10

        # Ranks must be 1..10 in order
        returned_ranks = [r[1] for r in result]
        assert returned_ranks == list(range(1, 11)), (
            f"Expected ranks 1..10, got {returned_ranks}"
        )

        # Scores must be descending (or equal)
        returned_scores = [r[2] for r in result]
        for i in range(len(returned_scores) - 1):
            assert returned_scores[i] >= returned_scores[i + 1], (
                f"Score at rank {i + 1} ({returned_scores[i]}) < "
                f"score at rank {i + 2} ({returned_scores[i + 1]})"
            )

        # Rank 1 should have the highest score (score for candidate 200 = 2.0)
        assert result[0][2] == pytest.approx(200 * 0.01, abs=1e-5)

    def test_select_top_n_tie_breaking_by_behavioral(self):
        """When scores are equal, behavioral_fit breaks the tie (descending)."""
        ids = ["CAND_0000001", "CAND_0000002", "CAND_0000003"]
        scores = np.array([0.5, 0.5, 0.5], dtype=np.float32)  # all equal

        # Give different behavioral_fit values
        d1 = _FakeDimScores(behavioral_fit=0.9)
        d2 = _FakeDimScores(behavioral_fit=0.3)
        d3 = _FakeDimScores(behavioral_fit=0.6)
        dims = [d1, d2, d3]

        result = select_top_n(ids, scores, dims, n=3)

        # Rank 1 should be CAND_0000001 (highest behavioral = 0.9)
        assert result[0][0] == "CAND_0000001", (
            f"Expected CAND_0000001 at rank 1, got {result[0][0]}"
        )

    def test_select_top_n_empty_input(self):
        """select_top_n with empty input must return empty list."""
        result = select_top_n([], np.array([]), [], n=100)
        assert result == []

    def test_select_top_n_result_format(self):
        """Each item in result must be a 4-tuple (str, int, float, DimScores)."""
        ids, scores, dims = self._make_candidates(10)
        result = select_top_n(ids, scores, dims, n=5)

        for item in result:
            assert len(item) == 4
            cand_id, rank, score, ds = item
            assert isinstance(cand_id, str)
            assert isinstance(rank, int)
            assert isinstance(score, float)
            assert hasattr(ds, "behavioral_fit")
