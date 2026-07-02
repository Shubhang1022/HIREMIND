"""Phase 1 pre-computation pipeline — orchestrates end-to-end feature extraction.

Usage:
    python precompute.py --candidates <path> [options]

Exit codes:
    0 — success
    1 — candidates file not found
    2 — insufficient disk space (< 1 GB free)
    3 — model loading failed
    4 — validation error rate > 5%
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Inline the sentinel so the module is importable even without sentence_transformers
# ---------------------------------------------------------------------------
_ST_AVAILABLE = True
try:
    from sentence_transformers import SentenceTransformer  # noqa: F401  # availability check
except ImportError:
    _ST_AVAILABLE = False


# ---------------------------------------------------------------------------
# Project module imports (after sys.path is guaranteed to include project root)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.data.reader import CandidateStreamReader          # noqa: E402
from src.data.validator import validate                    # noqa: E402
from src.features.cache import FeatureCache                # noqa: E402
from src.features.embedding import EmbeddingEncoder        # noqa: E402
from src.features.structured import StructuredFeatureExtractor  # noqa: E402
from src.features.text_builder import build_candidate_text, build_jd_text  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
EMBEDDING_DIM_DEFAULT = 1024
MIN_DISK_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB


# ---------------------------------------------------------------------------
# Helper: flush one batch to disk
# ---------------------------------------------------------------------------

def _flush_batch(
    batch_texts: list[str],
    batch_features: list[dict],
    batch_id: int,
    encoder: EmbeddingEncoder,
    cache: FeatureCache,
    embedding_dim: int = EMBEDDING_DIM_DEFAULT,
) -> None:
    """Encode a mixed batch of texts and write embeddings + structured features.

    Candidates with empty text (honeypot / disqualified) receive a zero vector.
    Non-empty texts are encoded together in one forward pass for efficiency.
    """
    # Separate valid texts from empty placeholders
    valid_indices: list[int] = []
    valid_texts: list[str] = []
    for idx, text in enumerate(batch_texts):
        if text:
            valid_indices.append(idx)
            valid_texts.append(text)

    # Allocate full batch embedding array (zero-filled)
    arr = np.zeros((len(batch_texts), embedding_dim), dtype=np.float32)

    # Encode valid texts in one batch call
    if valid_texts:
        # For BGE v1.5 retrieval models, candidate texts are treated as "passages".
        encoded = encoder.encode_batch(valid_texts, normalize=True, bge_mode="passage")
        # encoded may come back with a different dim if model was loaded correctly
        actual_dim = encoded.shape[1]
        if actual_dim != embedding_dim:
            # Resize array to actual dim if needed
            arr = np.zeros((len(batch_texts), actual_dim), dtype=np.float32)
        for arr_idx, orig_idx in enumerate(valid_indices):
            arr[orig_idx] = encoded[arr_idx]

    # Persist to disk
    cache.save_embedding_batch(batch_id, arr)
    cache.save_structured_batch(batch_id, batch_features)


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def run_precompute(args: argparse.Namespace) -> int:
    """Run the full Phase 1 pipeline.

    Returns an exit code (0 = success).
    """
    t_start = time.monotonic()

    # ------------------------------------------------------------------ #
    # 1. Pre-flight checks                                                 #
    # ------------------------------------------------------------------ #

    # Check candidates file exists
    candidates_path = Path(args.candidates)
    if not candidates_path.is_file():
        print(f"[precompute] ERROR: candidates file not found: {candidates_path}", file=sys.stderr)
        return 1

    # Check disk space
    cache_parent = Path(args.cache_dir).resolve().parent
    try:
        usage = shutil.disk_usage(str(cache_parent))
        if usage.free < MIN_DISK_BYTES:
            print(
                f"[precompute] ERROR: insufficient disk space. "
                f"Need >= 1 GB free, have {usage.free / (1024**3):.2f} GB.",
                file=sys.stderr,
            )
            return 2
    except OSError as exc:
        print(f"[precompute] WARNING: could not check disk space: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # 2. Initialise components                                             #
    # ------------------------------------------------------------------ #
    reader = CandidateStreamReader(str(candidates_path), limit=args.limit)
    structured_extractor = StructuredFeatureExtractor()
    encoder = EmbeddingEncoder(model_name=args.model)
    cache = FeatureCache(cache_dir=args.cache_dir)

    # ------------------------------------------------------------------ #
    # 3. Load embedding model                                              #
    # ------------------------------------------------------------------ #
    if not _ST_AVAILABLE:
        print(
            "[precompute] ERROR: sentence_transformers is not installed. "
            "Install it with: pip install sentence-transformers",
            file=sys.stderr,
        )
        return 3

    if args.verbose:
        print(f"[precompute] Loading model: {args.model}")

    t_model = time.monotonic()
    try:
        encoder.load_model()
    except Exception as exc:  # noqa: BLE001
        print(f"[precompute] ERROR: model loading failed — {exc}", file=sys.stderr)
        return 3

    if args.verbose:
        print(f"[precompute] Model loaded in {time.monotonic() - t_model:.1f}s")

    # ------------------------------------------------------------------ #
    # 4. Encode JD text and save                                           #
    # ------------------------------------------------------------------ #
    if args.verbose:
        print("[precompute] Encoding JD text...")

    jd_text = build_jd_text()
    try:
        # For BGE v1.5 retrieval models, the job description is treated as a "query".
        jd_embedding = encoder.encode_single(jd_text, normalize=True, bge_mode="query").reshape(1, -1)
    except Exception as exc:  # noqa: BLE001
        print(f"[precompute] ERROR: JD encoding failed — {exc}", file=sys.stderr)
        return 3

    cache.save_jd_embedding(jd_embedding)
    embedding_dim = int(jd_embedding.shape[1])

    if args.verbose:
        print(f"[precompute] JD embedding saved (dim={embedding_dim})")

    # ------------------------------------------------------------------ #
    # 5. Stream candidates in batches                                      #
    # ------------------------------------------------------------------ #
    if args.verbose:
        print("[precompute] Processing candidates...")

    batch_texts: list[str] = []
    batch_features: list[dict] = []
    honeypot_flags_list: list[int] = []
    disqualifier_types: dict[str, str] = {}

    batch_id = 0
    total_read = 0          # lines attempted (valid JSON, not yet schema-validated)
    total_valid = 0         # lines that passed schema validate()
    total_invalid = 0       # lines that failed schema validate()
    honeypot_count = 0
    disqualified_count = 0
    id_to_index: dict[str, int] = {}
    index_to_id: list[str] = []
    global_idx = 0

    try:
        for candidate in reader:
            total_read += 1

            # Schema validation
            is_valid, _errors = validate(candidate)
            if not is_valid:
                total_invalid += 1
                continue

            candidate_id: str = candidate.get("candidate_id", "")
            id_to_index[candidate_id] = global_idx
            index_to_id.append(candidate_id)

            # Extract structured features
            features = structured_extractor.extract(
                candidate,
                batch_idx=batch_id,
                position_in_batch=len(batch_features),
            )

            # Track honeypot / disqualifier flag values for flags array
            if features["is_honeypot"]:
                honeypot_flags_list.append(1)
                honeypot_count += 1
            elif features["is_disqualified"]:
                reason = features["disqualifier_reason"]
                if reason == "consulting_only":
                    honeypot_flags_list.append(2)
                elif reason == "non_technical":
                    honeypot_flags_list.append(3)
                else:
                    honeypot_flags_list.append(2)  # fallback
                disqualifier_types[candidate_id] = reason
                disqualified_count += 1
            else:
                honeypot_flags_list.append(0)

            # Build candidate text (skip for disqualified / honeypots to save time)
            if not features["is_disqualified"] and not features["is_honeypot"]:
                text = build_candidate_text(candidate)
            else:
                text = ""  # zero-vector will be stored

            batch_texts.append(text)
            batch_features.append(features)
            global_idx += 1
            total_valid += 1

            # Flush when batch is full
            if len(batch_texts) >= args.batch_size:
                _flush_batch(
                    batch_texts, batch_features, batch_id, encoder, cache, embedding_dim
                )
                batch_texts = []
                batch_features = []
                batch_id += 1

            if args.verbose and total_valid % 5000 == 0:
                elapsed = time.monotonic() - t_start
                print(
                    f"[precompute] {total_valid} candidates processed "
                    f"({elapsed:.1f}s elapsed)"
                )

    except KeyboardInterrupt:
        elapsed = time.monotonic() - t_start
        print(
            f"\n[precompute] Interrupted. {total_valid} candidates processed in "
            f"{elapsed:.1f}s. Partial cache may be incomplete.",
            file=sys.stderr,
        )
        # Still flush whatever is in the current partial batch
        if batch_texts:
            _flush_batch(
                batch_texts, batch_features, batch_id, encoder, cache, embedding_dim
            )
            batch_id += 1
        return 0

    # ------------------------------------------------------------------ #
    # 6. Flush remaining partial batch                                     #
    # ------------------------------------------------------------------ #
    has_remaining = bool(batch_texts)
    if has_remaining:
        _flush_batch(
            batch_texts, batch_features, batch_id, encoder, cache, embedding_dim
        )

    # ------------------------------------------------------------------ #
    # 7. Check validation error rate                                       #
    # ------------------------------------------------------------------ #
    if total_read > 0:
        error_rate = total_invalid / total_read
        if error_rate > 0.05:
            print(
                f"[precompute] ERROR: validation error rate too high — "
                f"{total_invalid}/{total_read} = {error_rate:.1%} > 5%.",
                file=sys.stderr,
            )
            return 4

    # ------------------------------------------------------------------ #
    # 8. Save flags                                                        #
    # ------------------------------------------------------------------ #
    flags_array = np.array(honeypot_flags_list, dtype=np.uint8)
    cache.save_flags(flags_array, disqualifier_types)

    # ------------------------------------------------------------------ #
    # 9. Save meta                                                         #
    # ------------------------------------------------------------------ #
    stats = reader.get_stats()
    num_batches = batch_id + (1 if has_remaining else 0)

    meta: dict = {
        "created_at": datetime.now().isoformat(),
        "total_candidates": total_valid,
        "valid_candidates": total_valid - honeypot_count - disqualified_count,
        "skipped_lines": stats["skipped"],
        "honeypot_count": honeypot_count,
        "disqualified_count": disqualified_count,
        "embedding_model": args.model,
        "embedding_dim": embedding_dim,
        "batch_size": args.batch_size,
        "num_batches": num_batches,
        "id_to_index": id_to_index,
        "index_to_id": index_to_id,
    }
    cache.save_meta(meta)

    # ------------------------------------------------------------------ #
    # 10. Summary                                                          #
    # ------------------------------------------------------------------ #
    elapsed = time.monotonic() - t_start
    valid_for_ranking = total_valid - honeypot_count - disqualified_count

    print(f"[precompute] Done. {total_valid} candidates processed in {elapsed:.1f}s")
    print(f"[precompute] Honeypots detected: {honeypot_count}")
    print(f"[precompute] Hard disqualified: {disqualified_count}")
    print(f"[precompute] Valid candidates for ranking: {valid_for_ranking}")
    print(f"[precompute] Feature cache written to: {args.cache_dir}")

    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="precompute.py",
        description="Phase 1 pre-computation: extract features and embeddings for all candidates.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--candidates",
        required=True,
        metavar="PATH",
        help="Path to candidates.jsonl (required)",
    )
    parser.add_argument(
        "--cache-dir",
        default="./feature_cache",
        metavar="PATH",
        help="Output cache directory",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=512,
        metavar="INT",
        help="Candidates per embedding batch",
    )
    parser.add_argument(
        "--model",
        default="BAAI/bge-large-en-v1.5",
        metavar="STR",
        help="Embedding model name or local path",
    )
    parser.add_argument(
        "--config",
        default="./config/ranking_config.yaml",
        metavar="PATH",
        help="YAML config file (currently informational)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        metavar="INT",
        help="CPU workers (reserved for future parallelism)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="INT",
        help="Stop after N candidates (for testing)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress every 5000 candidates",
    )
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(run_precompute(args))
