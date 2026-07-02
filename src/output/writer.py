"""SubmissionWriter — write and validate the final submission CSV."""
from __future__ import annotations

import csv
import re
from pathlib import Path

# Pattern for valid candidate IDs (CAND_ followed by exactly 7 digits).
_CANDIDATE_ID_RE = re.compile(r"^CAND_\d{7}$")
_MAX_REASONING_CHARS = 300


class SubmissionWriter:
    """Write and validate the submission CSV.

    Output format
    -------------
    Columns: candidate_id, rank, score, reasoning
    - Exactly 100 data rows
    - UTF-8 encoding, Unix line endings (\\n)
    - Scores formatted to 4 decimal places
    - Scores are monotonically non-increasing with rank
    """

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write(
        self,
        ranked_candidates: list[tuple[str, int, float, str]],
        output_path: str,
    ) -> None:
        """Write CSV with columns: candidate_id, rank, score, reasoning.

        Parameters
        ----------
        ranked_candidates:
            List of (candidate_id, rank, score, reasoning) sorted by rank.
        output_path:
            Destination file path. Created or overwritten.

        Raises
        ------
        ValueError
            If ranked_candidates is empty or output_path is invalid.
        """
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        from src.ranking.engine import validate_tuple
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            writer.writerow(["candidate_id", "rank", "score", "reasoning"])
            for item in ranked_candidates:
                validate_tuple(item, 4, "SubmissionWriter.write Loop", "(cand_id, rank, score, reasoning)")
                cand_id, rank, score, reasoning = item
                writer.writerow([
                    cand_id,
                    rank,
                    f"{float(score):.4f}",
                    str(reasoning),
                ])

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    def validate(self, output_path: str) -> list[str]:
        """Validate the CSV file. Returns a list of error strings (empty = valid).

        Checks
        ------
        - Exactly 100 data rows (header not counted)
        - Columns: candidate_id, rank, score, reasoning (in that order)
        - Ranks 1-100 each exactly once
        - Scores are monotonically non-increasing
        - All candidate_ids match pattern CAND_\\d{7}
        - Reasoning ≤ 300 chars per row
        - UTF-8 encodable
        """
        errors: list[str] = []
        path = Path(output_path)

        if not path.exists():
            return [f"File not found: {output_path}"]

        # --- UTF-8 check ---
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            return [f"File is not valid UTF-8: {exc}"]

        lines = content.splitlines()
        if not lines:
            return ["File is empty"]

        # --- Parse with csv ---
        try:
            rows = list(csv.reader(content.splitlines()))
        except csv.Error as exc:
            return [f"CSV parse error: {exc}"]

        if not rows:
            return ["CSV has no rows"]

        # --- Column header check ---
        expected_header = ["candidate_id", "rank", "score", "reasoning"]
        header = rows[0]
        if header != expected_header:
            errors.append(
                f"Header mismatch: expected {expected_header}, got {header}"
            )

        data_rows = rows[1:]

        # --- Row count ---
        if len(data_rows) != 100:
            errors.append(
                f"Expected exactly 100 data rows, got {len(data_rows)}"
            )

        if not data_rows:
            return errors

        # --- Per-row validation ---
        seen_ranks: set[int] = set()
        prev_score: float | None = None
        score_monotonic = True

        for row_num, row in enumerate(data_rows, start=2):  # 1-indexed; row 1 = header
            if len(row) != 4:
                errors.append(
                    f"Row {row_num}: expected 4 columns, got {len(row)}"
                )
                continue

            cand_id, rank_str, score_str, reasoning = row

            # Candidate ID format
            if not _CANDIDATE_ID_RE.match(cand_id):
                errors.append(
                    f"Row {row_num}: invalid candidate_id '{cand_id}' "
                    f"(must match CAND_\\d{{7}})"
                )

            # Rank
            try:
                rank = int(rank_str)
            except ValueError:
                errors.append(f"Row {row_num}: rank '{rank_str}' is not an integer")
                rank = -1

            if rank != -1:
                if rank < 1 or rank > 100:
                    errors.append(
                        f"Row {row_num}: rank {rank} out of range [1, 100]"
                    )
                elif rank in seen_ranks:
                    errors.append(f"Row {row_num}: duplicate rank {rank}")
                else:
                    seen_ranks.add(rank)

            # Score
            try:
                score = float(score_str)
            except ValueError:
                errors.append(f"Row {row_num}: score '{score_str}' is not a float")
                score = None

            if score is not None and prev_score is not None:
                if score > prev_score + 1e-9:  # allow tiny float rounding
                    score_monotonic = False
            if score is not None:
                prev_score = score

            # Reasoning length
            if len(reasoning) > _MAX_REASONING_CHARS:
                errors.append(
                    f"Row {row_num}: reasoning is {len(reasoning)} chars "
                    f"(max {_MAX_REASONING_CHARS})"
                )

        if not score_monotonic:
            errors.append(
                "Scores are not monotonically non-increasing (expected rank 1 → 100 descending)"
            )

        # --- Ranks completeness ---
        if len(data_rows) == 100 and len(seen_ranks) == 100:
            expected_ranks = set(range(1, 101))
            missing = expected_ranks - seen_ranks
            if missing:
                errors.append(f"Missing ranks: {sorted(missing)}")

        # --- Candidate eligibility / fallback checks ---
        if data_rows:
            # Rank #1 candidate checks (first row in CSV)
            first_row = data_rows[0]
            try:
                first_score = float(first_row[2])
                if first_score == 0.0:
                    errors.append("Validation failed: Rank #1 score is 0.0.")
                    errors.append("Validation failed: Top candidate match percentage is 0.")
            except (ValueError, IndexError):
                pass

            # All candidates are Weak Match checks
            all_weak = True
            for row in data_rows:
                try:
                    s = float(row[2])
                    # If scores are scaled 0-1, threshold is 0.40. If scaled 0-100, threshold is 40.0.
                    # Since scores could theoretically be either, check against both.
                    if s >= 40.0 or (0.40 <= s <= 1.0):
                        all_weak = False
                        break
                except (ValueError, IndexError):
                    pass
            if all_weak:
                errors.append("Validation failed: All candidates are Weak Match.")

        return errors
