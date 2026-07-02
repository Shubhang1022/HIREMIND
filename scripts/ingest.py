"""Ingest wrapper — alias for the precompute pipeline.

This script writes to the feature cache which is consumed by rank.py (and,
in a production setup, could be ingested into PostgreSQL via the backend API).

Usage:
    python scripts/ingest.py --candidates ./India_runs_data_and_ai_challenge/candidates.jsonl
    python scripts/ingest.py --candidates ./data/candidates.jsonl --limit 100 --verbose

All arguments are forwarded verbatim to precompute.py.
"""
import os
import subprocess
import sys

if __name__ == "__main__":
    args = sys.argv[1:]
    # Resolve path to precompute.py relative to this script's parent directory
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    precompute_script = os.path.join(project_root, "precompute.py")
    result = subprocess.run(
        [sys.executable, precompute_script] + args,
        cwd=project_root,
    )
    sys.exit(result.returncode)
