"""Unit tests for the new JD-First Candidate Retrieval Architecture streaming and pre-filtering."""
from __future__ import annotations

import io
import json
import pytest
from pathlib import Path

import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_ROOT = _PROJECT_ROOT / "backend"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.api.v1.endpoints.platform import (
    stream_candidates,
    enrich_candidate_with_intelligence,
    standardize_candidate,
)

def test_stream_jsonl():
    data = '{"candidate_id": "C1", "name": "Alice"}\n{"candidate_id": "C2", "name": "Bob"}'
    file_like = io.StringIO(data)
    candidates = list(stream_candidates(file_like, "test.jsonl"))
    assert len(candidates) == 2
    assert candidates[0]["candidate_id"] == "C1"
    assert candidates[1]["candidate_id"] == "C2"

def test_stream_json_list():
    data = '[{"candidate_id": "C1", "name": "Alice"}, {"candidate_id": "C2", "name": "Bob"}]'
    file_like = io.StringIO(data)
    candidates = list(stream_candidates(file_like, "test.json"))
    assert len(candidates) == 2
    assert candidates[0]["candidate_id"] == "C1"
    assert candidates[1]["candidate_id"] == "C2"

def test_stream_csv():
    data = "candidate_id,name\nC1,Alice\nC2,Bob"
    file_like = io.StringIO(data)
    candidates = list(stream_candidates(file_like, "test.csv"))
    assert len(candidates) == 2
    assert candidates[0]["candidate_id"] == "C1"
    assert candidates[1]["candidate_id"] == "C2"

def test_jd_enrichment():
    cand = {
        "candidate_id": "C1",
        "profile": {
            "candidate_name": "Charlie",
            "current_title": "MLOps Engineer",
            "years_of_experience": 5.0,
        },
        "skills": ["Python", "Kubeflow", "Docker"]
    }
    enriched = enrich_candidate_with_intelligence(cand)
    assert enriched["candidate_role_category"] == "MLOPS"
    assert enriched["is_disqualified"] is False
