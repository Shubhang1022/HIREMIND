# Database Schema — Feature Store & Intermediate Storage

**Architecture Note**: This system uses no relational or document database. All intermediate state is stored as flat files (numpy arrays + pickle) in a `feature_cache/` directory. This is intentional — database overhead (startup time, query overhead) is incompatible with the 5-minute runtime constraint.

---

## 1. Storage Strategy Overview

```
feature_cache/
├── meta.json                    # Index of all cached batches and metadata
├── embeddings/
│   ├── batch_000.npy            # [512, 384] float32 — candidate text embeddings
│   ├── batch_001.npy            # Next batch of 512
│   └── ...                      # ~196 batches for 100K candidates
├── structured/
│   ├── batch_000.pkl            # List[dict] — structured features per candidate
│   ├── batch_001.pkl
│   └── ...
├── flags/
│   ├── honeypot_flags.npy       # [100K] uint8 — 0=clean, 1=honeypot, 2=disqualified
│   └── disqualifier_types.pkl   # dict[candidate_id → disqualifier_reason]
├── jd/
│   └── jd_embedding.npy         # [1, 384] float32 — JD text embedding
└── scores/
    └── dimension_scores.npy     # [100K, 6] float32 — pre-computed dim scores
                                 # (written at end of Phase 1)
```

---

## 2. Embedding Storage Schema

### 2.1 File: `embeddings/batch_{i:03d}.npy`

| Attribute | Value |
|---|---|
| Format | NumPy binary (.npy) |
| Shape | `[batch_size, 384]` — typically `[512, 384]` |
| Dtype | `float32` |
| Size per batch | 512 × 384 × 4 bytes ≈ 786 KB |
| Total (100K) | ~154 MB |

**Content**: The normalized sentence embedding for each candidate's concatenated text corpus:
```
text_corpus = f"{headline} {summary} " + " ".join(desc for desc in career_descriptions) + " " + " ".join(skill_names)
```

The text is truncated to 512 tokens (model's context limit) during encoding. For candidates with long career histories, the most recent roles are prioritized.

### 2.2 File: `jd/jd_embedding.npy`

| Attribute | Value |
|---|---|
| Shape | `[1, 384]` |
| Dtype | `float32` |
| Size | 1.5 KB |

**Content**: The normalized sentence embedding for the full JD text. Computed once, used in every batch similarity computation.

---

## 3. Structured Feature Schema

### 3.1 File: `structured/batch_{i:03d}.pkl`

Each batch file is a Python `list` of `dict` objects (one per candidate). Each dict has the following schema:

```python
{
    # Identity
    "candidate_id": str,                    # "CAND_0001234"
    "batch_idx": int,                       # Which batch file this came from
    "position_in_batch": int,               # Index within batch
    
    # Experience features
    "years_exp": float,                     # profile.years_of_experience
    "derived_years_exp": float,             # sum(duration_months)/12 from career_history
    "career_history_count": int,            # Number of roles
    "total_duration_months": int,           # Sum of all duration_months
    "current_role_months": int,             # Duration of current role
    "longest_tenure_months": int,           # Longest single role
    
    # Company type features
    "product_company_months": int,          # Months at non-consulting companies
    "consulting_company_months": int,       # Months at consulting firms
    "product_company_ratio": float,         # product / total (0.0–1.0)
    "consulting_only": bool,                # Hard disqualifier flag
    "max_company_size_band": int,           # Ordinal encoding of largest company size
    "current_company_size_band": int,       # Ordinal encoding of current company size
    
    # Title/seniority features
    "title_seniority_scores": list[int],    # Seniority level per role [1..6]
    "current_seniority_level": int,         # Seniority of current title
    "seniority_trend": float,               # Linear regression slope of seniority over time
    "non_technical_title_only": bool,       # Hard disqualifier flag
    
    # Skills features
    "total_skills_count": int,
    "ai_ml_skill_count": int,               # Skills matching JD-relevant AI/ML skill list
    "core_jd_skill_count": int,             # Skills matching JD must-haves specifically
    "skill_depth_score": float,             # Weighted skill depth (see RankingLogic.md)
    "has_embedding_retrieval": bool,        # sentence-transformers/BGE/E5 etc.
    "has_vector_db": bool,                  # Pinecone/Weaviate/Qdrant/Milvus/FAISS etc.
    "has_python_advanced": bool,            # Python at advanced or expert level
    "has_evaluation_framework": bool,       # NDCG/MRR/MAP/LTR keywords
    "keyword_stuffing_penalty": float,      # 0.4 if stuffing detected, 1.0 otherwise
    "llm_only_recency_penalty": float,      # 0.3 if all AI exp within 12 months, 1.0 otherwise
    
    # Production evidence
    "production_evidence_score": float,     # 0.0–1.0 from description keyword analysis
    "has_ab_testing": bool,                 # A/B test references in descriptions
    "has_latency_sla": bool,                # Latency/throughput/SLA references
    "has_real_users": bool,                 # "real users", "serving X users" etc.
    
    # Career progression
    "seniority_trajectory_bonus": float,   # 0.2 if non-decreasing seniority
    "job_hop_penalty": float,              # 0.15 if title-chaser detected
    "stagnation_penalty": float,           # 0.1 if same title >48 months
    "leadership_evidence_score": float,    # 0.0–1.0 from description leadership patterns
    
    # Behavioral signals
    "open_to_work": bool,
    "days_since_active": int,              # Computed from last_active_date
    "notice_period_days": int,
    "recruiter_response_rate": float,
    "avg_response_time_hours": float,
    "github_activity_score": float,        # -1 if no GitHub
    "interview_completion_rate": float,
    "offer_acceptance_rate": float,        # -1 if no history
    "verified_email": bool,
    "verified_phone": bool,
    "linkedin_connected": bool,
    "saved_by_recruiters_30d": int,
    "profile_completeness_score": float,   # 0–100
    "skill_assessment_scores": dict,       # {skill_name: score 0-100}
    
    # Location/logistics
    "country": str,
    "location_city": str,
    "location_fit_score": float,           # 0.2–1.0 per scoring rubric
    "salary_min_lpa": float,
    "salary_max_lpa": float,
    "salary_alignment_score": float,       # 0.3–1.0 per scoring rubric
    "preferred_work_mode": str,
    "willing_to_relocate": bool,
    
    # Honeypot flags
    "is_honeypot": bool,
    "honeypot_flags": list[str],           # List of triggered flag names
    "honeypot_suspicion_score": float,     # 0.0–1.0 cumulative suspicion
    "is_disqualified": bool,               # True if any hard disqualifier
    "disqualifier_reason": str,            # "consulting_only", "non_technical", "honeypot", ""
}
```

**Size estimate**: Each dict ~2 KB serialized. 100K dicts in 196 batches of 512 = ~200 MB total.

---

## 4. Flag Storage Schema

### 4.1 File: `flags/honeypot_flags.npy`

| Attribute | Value |
|---|---|
| Shape | `[100000]` |
| Dtype | `uint8` |
| Values | 0=clean, 1=honeypot (definitive), 2=consulting-only disqualified, 3=non-technical disqualified, 4=suspicion (minor penalty) |
| Size | ~100 KB |

The index corresponds to the order candidates appear in the original JSONL. The `meta.json` maps `candidate_id → array_index`.

### 4.2 File: `flags/disqualifier_types.pkl`

Python `dict[str, str]` mapping `candidate_id → disqualifier_reason` for all disqualified candidates. Used for audit logging and debugging.

---

## 5. Dimension Scores Storage

### 5.1 File: `scores/dimension_scores.npy`

| Attribute | Value |
|---|---|
| Shape | `[100000, 8]` |
| Dtype | `float32` |
| Columns | [semantic_fit, experience_quality, career_progression, behavioral_signals, logistics_fit, profile_integrity, disqualifier_multiplier, semantic_similarity] |
| Size | ~3.2 MB |

Written at the end of Phase 1 (or the beginning of Phase 2 if computing dimensions during Phase 2). In Phase 2, this array is loaded directly for final score assembly without re-reading any candidate data.

**Column index mapping**:
```python
DIM_SEMANTIC = 0
DIM_EXPERIENCE = 1
DIM_PROGRESSION = 2
DIM_BEHAVIORAL = 3
DIM_LOGISTICS = 4
DIM_INTEGRITY = 5
DIM_DISQUALIFIER = 6   # 0.0 or 1.0
DIM_SIM_SCORE = 7      # Raw cosine similarity (before normalization)
```

---

## 6. Meta Index

### 6.1 File: `meta.json`

```json
{
    "created_at": "2025-01-01T00:00:00",
    "total_candidates": 100000,
    "valid_candidates": 99847,
    "skipped_lines": 153,
    "honeypot_count": 82,
    "disqualified_count": 8420,
    "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
    "embedding_dim": 384,
    "batch_size": 512,
    "num_batches": 196,
    "id_to_index": {
        "CAND_0000001": 0,
        "CAND_0000002": 1,
        ...
    },
    "index_to_id": ["CAND_0000001", "CAND_0000002", ...]
}
```

**Note**: `id_to_index` maps candidate_id to its position in the `[100K]` score arrays. This is the lookup table used in Phase 2 to map top-100 scores back to candidate_ids for CSV output.

---

## 7. Output Schema

### 7.1 Final Submission: `submission.csv`

```
candidate_id,rank,score,reasoning
CAND_0001234,1,0.8934,"7.2 yrs exp; ML Engineer at FinTech product startup; production FAISS + sentence-transformer deployment; Pune-based; 15d notice; response rate 82%."
CAND_0002345,2,0.8801,"6.8 yrs; Senior AI Engineer; evidence of A/B test and latency SLA in descriptions; BGE + Weaviate skills; Delhi NCR; willing to relocate; 30d notice."
...
```

| Column | Type | Constraints |
|---|---|---|
| candidate_id | string | Must exist in candidates.jsonl; pattern CAND_XXXXXXX |
| rank | integer | 1–100 inclusive, each exactly once |
| score | float | 4 decimal places, monotonically non-increasing with rank |
| reasoning | string | UTF-8, no embedded commas or unescaped quotes; ≤300 chars |

### 7.2 Audit Log: `ranking_audit.json` (optional, for defense interview)

```json
{
    "run_timestamp": "2025-01-01T00:00:00",
    "total_candidates_evaluated": 99847,
    "hard_disqualified": 8420,
    "honeypots_detected": 82,
    "honeypots_in_top_100": 0,
    "score_statistics": {
        "min": 0.0,
        "max": 0.9134,
        "mean": 0.2341,
        "top_100_min": 0.4123,
        "top_100_max": 0.9134
    },
    "dimension_weight_used": {
        "semantic_skill_fit": 0.30,
        "experience_quality": 0.25,
        "career_progression": 0.15,
        "behavioral_signals": 0.15,
        "logistics_fit": 0.10,
        "profile_integrity": 0.05
    },
    "top_100_summary": [
        {"rank": 1, "candidate_id": "CAND_0001234", "scores": [0.92, 0.88, 0.75, 0.82, 1.0, 0.9]},
        ...
    ]
}
```

This file is invaluable for the Stage 5 defend-your-work interview. It shows exactly what the system did and why each candidate was ranked where they were.

---

## 8. In-Memory vs Disk Strategy

| Data | Strategy | Rationale |
|---|---|---|
| Raw JSONL candidates | Stream (never in memory) | 465 MB + parsed dict overhead would exceed budget |
| Candidate text for embedding | Buffer 512 at a time | Trade throughput for memory safety |
| Embeddings | Load 10K at a time during Phase 2 | 10K × 384 × 4 = 15 MB — efficient batch similarity |
| Structured features | All 100K in memory during Phase 2 | 100K × ~2 KB = ~200 MB — affordable, fast random access |
| Dimension score arrays | All 100K in memory during scoring | 100K × 8 × 4 = 3.2 MB — trivial |
| Final top-100 | In memory | Trivial size |

**Phase 2 peak memory estimate**:
- Structured features (all 100K): ~200 MB
- Embedding batch (10K): ~15 MB
- Score arrays: ~10 MB
- Model weights: ~300 MB
- **Total Phase 2 peak**: ~525 MB — well within 16 GB
