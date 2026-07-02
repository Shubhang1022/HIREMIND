"""Phase 2 ranking pipeline — load feature cache, score, select top-N, write CSV.

Usage:
    python rank.py --jd config/job_description.json --out submission.csv [options]

Exit codes (per docs/API_Spec.md §1.2):
    0 — success
    1 — feature cache not found
    2 — JD file not found
    3 — output validation failed
    5 — score monotonicity check failed
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

# Ensure project root is on sys.path so src.* imports resolve.
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_BACKEND_ROOT = _PROJECT_ROOT / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from src.features.cache import FeatureCache
from src.ranking.assembler import ScoreAssembler
from src.ranking.selector import select_top_n
from src.ranking.reasoning import ReasoningGenerator
from src.output.writer import SubmissionWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(config_path: str | None) -> dict:
    """Load YAML ranking config. Returns empty dict if path is None or missing."""
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.is_file():
        return {}
    try:
        import yaml  # type: ignore[import]
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return {}


def _build_audit_log(
    t_start: float,
    meta: dict,
    final_scores: np.ndarray,
    ranked: list,
    config: dict,
) -> dict:
    """Assemble the audit log dict."""
    valid_count = meta.get("valid_candidates", len(final_scores))
    nonzero_scores = final_scores[final_scores > 0]

    top_100_summary = []
    for cand_id, rank, score, dim_scores in ranked:
        top_100_summary.append(
            {
                "rank": rank,
                "candidate_id": cand_id,
                "final_score": round(float(score), 6),
                "dim_scores": {
                    "specialization_match": round(float(dim_scores.specialization_match), 4) if dim_scores else 0.0,
                    "required_skills_match": round(float(dim_scores.required_skills_match), 4) if dim_scores else 0.0,
                    "relevant_experience": round(float(dim_scores.relevant_experience), 4) if dim_scores else 0.0,
                    "semantic_similarity": round(float(dim_scores.semantic_similarity), 4) if dim_scores else 0.0,
                    "seniority_match": round(float(getattr(dim_scores, "seniority_match", 0.5)), 4) if dim_scores else 0.5,
                    "domain_expertise": round(float(getattr(dim_scores, "domain_expertise", 0.5)), 4) if dim_scores else 0.5,
                    "career_growth": round(float(dim_scores.career_growth), 4) if dim_scores else 0.0,
                    "behavioral_fit": round(float(dim_scores.behavioral_fit), 4) if dim_scores else 0.0,
                    "integrity": round(float(dim_scores.integrity), 4) if dim_scores else 0.0,
                    "education": round(float(dim_scores.education), 4) if dim_scores else 0.0,
                },
            }
        )

    weights = config.get(
        "weights",
        {
            "specialization_match": 0.30,
            "required_skills_match": 0.20,
            "relevant_experience": 0.15,
            "semantic_similarity": 0.10,
            "seniority_match": 0.05,
            "domain_expertise": 0.05,
            "career_growth": 0.05,
            "behavioral_fit": 0.05,
            "integrity": 0.05,
        },
    )

    return {
        "run_timestamp": datetime.now().isoformat(),
        "phase2_runtime_seconds": round(time.monotonic() - t_start, 2),
        "total_candidates_evaluated": meta.get("total_candidates", len(final_scores)),
        "valid_candidates": valid_count,
        "hard_disqualified": meta.get("disqualified_count", 0),
        "honeypots_detected": meta.get("honeypot_count", 0),
        "honeypots_in_top_100": sum(
            1 for _, _, _, ds in ranked if getattr(ds, "disqualifier_multiplier", 1.0) == 0.0
        ),
        "score_statistics": {
            "min": float(final_scores.min()) if len(final_scores) else 0.0,
            "max": float(final_scores.max()) if len(final_scores) else 0.0,
            "mean": float(final_scores.mean()) if len(final_scores) else 0.0,
            "top_100_min": round(ranked[-1][2], 4) if ranked else 0.0,
            "top_100_max": round(ranked[0][2], 4) if ranked else 0.0,
        },
        "dimension_weight_used": weights,
        "top_100_summary": top_100_summary,
    }


# ---------------------------------------------------------------------------
# Core rank pipeline
# ---------------------------------------------------------------------------

def run_rank(args: argparse.Namespace) -> int:
    """Run the Phase 2 ranking pipeline. Returns exit code."""
    t_start = time.monotonic()
    verbose = args.verbose

    # ------------------------------------------------------------------ #
    # 1. Pre-flight checks                                                 #
    # ------------------------------------------------------------------ #
    cache_dir = Path(args.cache_dir)
    meta_path = cache_dir / "meta.json"
    if not cache_dir.is_dir() or not meta_path.is_file():
        print(
            f"[rank] ERROR: feature cache not found at '{cache_dir}' "
            "(meta.json missing). Run precompute.py first.",
            file=sys.stderr,
        )
        return 1

    jd_path = Path(args.jd)
    if not jd_path.is_file():
        print(
            f"[rank] ERROR: JD file not found: '{jd_path}'.",
            file=sys.stderr,
        )
        return 2

    # ------------------------------------------------------------------ #
    # 2. Load config                                                       #
    # ------------------------------------------------------------------ #
    config = _load_config(args.config)

    with open(jd_path, "r", encoding="utf-8") as fh:
        jd_dict = json.load(fh)

    # ------------------------------------------------------------------ #
    # 3. Run Unified Ranking Engine                                        #
    # ------------------------------------------------------------------ #
    if verbose:
        print(f"[rank] Loading feature cache from {cache_dir}/")
        print("[rank] Running unified ranking pipeline...")

    from src.features.embedding import EmbeddingEncoder
    try:
        from app.core.config import settings  # type: ignore
    except ImportError:
        from backend.app.core.config import settings
    from src.ranking.engine import UnifiedRankingEngine
    import asyncio

    encoder = EmbeddingEncoder(model_name=settings.embedding_model)

    # Disable eligibility filtering for CLI to guarantee exactly top_n output
    cli_config = dict(config) if config else {}
    cli_config["apply_eligibility"] = False
    cli_config["anonymize_mode"] = True
    engine = UnifiedRankingEngine(encoder=encoder, config=cli_config)
    
    from src.ranking.engine import validate_tuple
    res_tuple = asyncio.run(engine.rank_cached_candidates(
        cache_dir=str(cache_dir),
        jd_dict=jd_dict,
        top_n=args.top_n,
        call_llm=bool(settings.openrouter_api_key)
    ))
    assert isinstance(res_tuple, tuple), f"Expected tuple from rank_cached_candidates, got {type(res_tuple).__name__}"
    print("RES_TUPLE CLI:", type(res_tuple), len(res_tuple), res_tuple)
    validate_tuple(res_tuple, 3, "rank.py main CLI", "(results, ranked, final_scores)")
    results, ranked, final_scores = res_tuple

    # Format the results to match rank.py output expected by SubmissionWriter
    ranked_with_reasoning = [
        (res["candidate_id"], res["rank"], res["ai_score"], res["reasoning"])
        for res in results
    ]

    # ------------------------------------------------------------------ #
    # 7. Write CSV                                                         #
    # ------------------------------------------------------------------ #
    out_path = args.out
    writer = SubmissionWriter()

    if verbose:
        print(f"[rank] Writing {out_path}...")

    writer.write(ranked_with_reasoning, out_path)

    # ------------------------------------------------------------------ #
    # 8. Validate                                                          #
    # ------------------------------------------------------------------ #
    errors = writer.validate(out_path)

    if errors:
        # Separate monotonicity errors from other validation errors
        mono_errors = [e for e in errors if "monoton" in e.lower()]
        other_errors = [e for e in errors if "monoton" not in e.lower()]

        if other_errors:
            print("[rank] ERROR: Output validation failed:", file=sys.stderr)
            for e in other_errors:
                print(f"  - {e}", file=sys.stderr)
            return 3

        if mono_errors:
            print("[rank] ERROR: Score monotonicity check failed:", file=sys.stderr)
            for e in mono_errors:
                print(f"  - {e}", file=sys.stderr)
            return 5

    if verbose:
        print(f"[rank] Validation: {args.top_n} rows, scores monotonic OK")

    # ------------------------------------------------------------------ #
    # 9. Audit log                                                         #
    # ------------------------------------------------------------------ #
    if args.audit_log:
        with open(meta_path, "r", encoding="utf-8") as fh:
            cache_meta = json.load(fh)
        audit = _build_audit_log(
            t_start,
            cache_meta,
            final_scores,
            ranked,  # use dim_scores version for detail
            config,
        )
        audit_path = Path(args.audit_log)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "w", encoding="utf-8") as fh:
            json.dump(audit, fh, indent=2, ensure_ascii=False)
        if verbose:
            print(f"[rank] Audit log written to {args.audit_log}")

    # ------------------------------------------------------------------ #
    # 10. Summary                                                          #
    # ------------------------------------------------------------------ #
    elapsed = time.monotonic() - t_start
    if verbose:
        top_score = ranked_with_reasoning[0][2] if ranked_with_reasoning else 0.0
        print(
            f"[rank] Done. Top score: {top_score:.4f}. "
            f"Total time: {elapsed:.1f}s"
        )
    else:
        print(f"[rank] submission written to {out_path} ({args.top_n} candidates, {elapsed:.1f}s)")

    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rank.py",
        description=(
            "Phase 2 ranking: load feature cache, score all candidates, "
            "select top-N, generate reasoning, write CSV."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
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
        help="Feature cache directory (written by precompute.py)",
    )
    parser.add_argument(
        "--config",
        default="./config/ranking_config.yaml",
        metavar="PATH",
        help="YAML config path",
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
        default="./ranking_audit.json",
        metavar="PATH",
        help="Optional audit log path (JSON)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output",
    )
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(run_rank(args))
