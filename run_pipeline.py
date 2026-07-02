"""Combined entry point — runs precompute.py then rank.py sequentially.

Usage:
    python run_pipeline.py \\
        --candidates <path> \\
        --jd <path> \\
        --out submission.csv \\
        [--cache-dir ./feature_cache] \\
        [--config ./config/ranking_config.yaml] \\
        [--batch-size 512] \\
        [--model sentence-transformers/all-MiniLM-L6-v2] \\
        [--top-n 100] \\
        [--audit-log ./ranking_audit.json] \\
        [--verbose]

Exit codes mirror those of the sub-scripts (see precompute.py and rank.py).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure project root on path.
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from precompute import run_precompute, _build_parser as _precompute_parser
from rank import run_rank, _build_parser as _rank_parser


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_pipeline.py",
        description=(
            "One-shot pipeline: Phase 1 feature pre-computation followed by "
            "Phase 2 ranking in a single invocation."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # Phase 1 args
    parser.add_argument(
        "--candidates",
        required=True,
        metavar="PATH",
        help="Path to candidates.jsonl (required)",
    )
    parser.add_argument(
        "--jd",
        default="./config/job_description.json",
        metavar="PATH",
        help="Path to JD JSON file",
    )
    parser.add_argument(
        "--out",
        default="./submission.csv",
        metavar="PATH",
        help="Output CSV path",
    )
    parser.add_argument(
        "--cache-dir",
        default="./feature_cache",
        metavar="PATH",
        help="Feature cache directory",
    )
    parser.add_argument(
        "--config",
        default="./config/ranking_config.yaml",
        metavar="PATH",
        help="YAML config path",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=512,
        metavar="INT",
        help="Embedding batch size for precompute phase",
    )
    parser.add_argument(
        "--model",
        default="BAAI/bge-large-en-v1.5",
        metavar="STR",
        help="Embedding model name or local path",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        metavar="INT",
        help="CPU workers for feature extraction (Phase 1)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=100,
        metavar="INT",
        help="Number of candidates to output",
    )
    parser.add_argument(
        "--audit-log",
        default=None,
        metavar="PATH",
        help="Optional audit log path (JSON)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="INT",
        help="Limit candidates processed (for testing)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output for both phases",
    )
    return parser


def _make_precompute_args(args: argparse.Namespace) -> argparse.Namespace:
    """Build the Namespace that precompute.run_precompute expects."""
    import argparse as ap
    ns = ap.Namespace(
        candidates=args.candidates,
        cache_dir=args.cache_dir,
        batch_size=args.batch_size,
        model=args.model,
        config=args.config,
        workers=args.workers,
        limit=getattr(args, "limit", None),
        verbose=args.verbose,
    )
    return ns


def _make_rank_args(args: argparse.Namespace) -> argparse.Namespace:
    """Build the Namespace that rank.run_rank expects."""
    import argparse as ap
    ns = ap.Namespace(
        jd=args.jd,
        out=args.out,
        cache_dir=args.cache_dir,
        config=args.config,
        top_n=args.top_n,
        audit_log=getattr(args, "audit_log", None),
        verbose=args.verbose,
    )
    return ns


def run_pipeline(args: argparse.Namespace) -> int:
    """Run Phase 1 then Phase 2. Returns exit code of the failing phase, or 0."""
    t_total = time.monotonic()

    # ------------------------------------------------------------------ #
    # Phase 1: precompute                                                  #
    # ------------------------------------------------------------------ #
    if args.verbose:
        print("[pipeline] === Phase 1: precompute ===")

    precompute_args = _make_precompute_args(args)
    code = run_precompute(precompute_args)
    if code != 0:
        print(
            f"[pipeline] Phase 1 (precompute) failed with exit code {code}.",
            file=sys.stderr,
        )
        return code

    if args.verbose:
        print("[pipeline] Phase 1 complete.\n")

    # ------------------------------------------------------------------ #
    # Phase 2: rank                                                        #
    # ------------------------------------------------------------------ #
    if args.verbose:
        print("[pipeline] === Phase 2: rank ===")

    rank_args = _make_rank_args(args)
    code = run_rank(rank_args)
    if code != 0:
        print(
            f"[pipeline] Phase 2 (rank) failed with exit code {code}.",
            file=sys.stderr,
        )
        return code

    elapsed = time.monotonic() - t_total
    if args.verbose:
        print(f"\n[pipeline] Pipeline complete in {elapsed:.1f}s.")
    else:
        print(f"[pipeline] Done in {elapsed:.1f}s. Output: {args.out}")

    return 0


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(run_pipeline(args))
