"""CLI script to parse a job description .docx and write config/job_description.json.

Usage:
    python scripts/parse_jd.py --jd ./India_runs_data_and_ai_challenge/job_description.docx
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse a .docx JD file and write structured JSON to config/job_description.json"
    )
    parser.add_argument(
        "--jd",
        required=True,
        help="Path to the .docx job description file",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path (default: config/job_description.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    # Resolve output path relative to workspace root (parent of scripts/)
    workspace_root = Path(__file__).parent.parent
    output_path = Path(args.output) if args.output else workspace_root / "config" / "job_description.json"

    # Import here so scripts/ can be run without installing the package
    sys.path.insert(0, str(workspace_root))
    from src.data.jd_parser import parse_jd_docx

    print(f"Parsing JD from: {args.jd}")
    jd = parse_jd_docx(args.jd)

    # Write JSON
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(jd, f, indent=2, ensure_ascii=False)

    # Print summary
    print(f"\n✓ Wrote structured JD to: {output_path}")
    print(f"  title:               {jd['title']}")
    print(f"  company:             {jd['company']}")
    print(f"  location:            {jd['location']}")
    print(f"  experience_years:    {jd['experience_years']}")
    print(f"  must_have_skills:    {jd['must_have_skills']}")
    print(f"  nice_to_have_skills: {jd['nice_to_have_skills']}")
    print(f"  hard_disqualifiers:  {jd['hard_disqualifiers']}")
    print(f"  preferred_locations: {jd['preferred_locations']}")
    print(f"  salary_range_lpa:    {jd['salary_range_lpa']}")
    print(f"  full_text length:    {len(jd['full_text'])} chars")

    return 0


if __name__ == "__main__":
    sys.exit(main())
