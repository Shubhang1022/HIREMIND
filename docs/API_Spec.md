# API Specification — AI Recruiter Copilot

**Version**: 1.0  
This document specifies the CLI interface, pre-computation script, internal Python module API, configuration schema, and output schema.

---

## 1. CLI Interface

### 1.1 Pre-Computation Script: `precompute.py`

Runs Phase 1: streams all candidates, extracts features, generates embeddings, writes feature cache to disk.

```bash
python precompute.py \
    --candidates ./India_runs_data_and_ai_challenge/candidates.jsonl \
    [--cache-dir ./feature_cache] \
    [--batch-size 512] \
    [--model sentence-transformers/all-MiniLM-L6-v2] \
    [--config ./config/ranking_config.yaml] \
    [--workers 4] \
    [--verbose]
```

**Arguments**:

| Argument | Type | Default | Required | Description |
|---|---|---|---|---|
| `--candidates` | path | — | Yes | Path to candidates.jsonl |
| `--cache-dir` | path | `./feature_cache` | No | Output directory for feature cache |
| `--batch-size` | int | 512 | No | Candidates per embedding batch |
| `--model` | string | `sentence-transformers/all-MiniLM-L6-v2` | No | Local embedding model name or path |
| `--config` | path | `./config/ranking_config.yaml` | No | YAML config file |
| `--workers` | int | 4 | No | Number of CPU workers for structured feature extraction |
| `--verbose` | flag | False | No | Print progress every 1000 candidates |

**Exit codes**:
- `0`: Success, feature cache written
- `1`: Input file not found
- `2`: Insufficient disk space (<1 GB free)
- `3`: Model loading failed
- `4`: Validation errors exceeded threshold (>5% of candidates invalid)

**Stdout (with `--verbose`)**:
```
[precompute] Loading model: sentence-transformers/all-MiniLM-L6-v2
[precompute] Model loaded in 3.2s
[precompute] Processing candidates...
[precompute] 10000 / 100000 (10%) — 42.3s elapsed
[precompute] 20000 / 100000 (20%) — 84.1s elapsed
...
[precompute] Honeypots detected: 82
[precompute] Hard disqualified (consulting-only): 7841
[precompute] Hard disqualified (non-technical): 612
[precompute] Valid candidates for ranking: 91465
[precompute] Writing feature cache to ./feature_cache/
[precompute] Feature cache written: 362 MB
[precompute] Total time: 187.3s
```

---

### 1.2 Ranking Script: `rank.py`

Runs Phase 2: loads feature cache, computes final scores, selects top 100, generates reasoning, writes CSV.

```bash
python rank.py \
    --jd ./India_runs_data_and_ai_challenge/job_description.json \
    --out ./submission.csv \
    [--cache-dir ./feature_cache] \
    [--config ./config/ranking_config.yaml] \
    [--top-n 100] \
    [--audit-log ./ranking_audit.json] \
    [--verbose]
```

**Arguments**:

| Argument | Type | Default | Required | Description |
|---|---|---|---|---|
| `--jd` | path | — | Yes | Path to JD JSON file |
| `--out` | path | `./submission.csv` | No | Output CSV path |
| `--cache-dir` | path | `./feature_cache` | No | Input feature cache directory |
| `--config` | path | `./config/ranking_config.yaml` | No | YAML config file |
| `--top-n` | int | 100 | No | Number of candidates to return (always 100 for submission) |
| `--audit-log` | path | None | No | Write audit JSON to this path |
| `--verbose` | flag | False | No | Verbose output |

**Exit codes**:
- `0`: Success, submission.csv written and validated
- `1`: Feature cache not found (run precompute.py first)
- `2`: JD file not found
- `3`: Output validation failed (format errors in generated CSV)
- `5`: Score monotonicity check failed

**Stdout (with `--verbose`)**:
```
[rank] Loading feature cache from ./feature_cache/
[rank] Loaded 100000 candidate features (91465 valid for ranking)
[rank] Computing JD embedding...
[rank] Computing semantic similarity scores...
[rank] Assembling dimension scores...
[rank] Final score computation complete
[rank] Top-100 selection done
[rank] Generating reasoning strings...
[rank] Validation: 100 rows, scores monotonic ✓
[rank] Writing submission.csv...
[rank] Done. Total time: 43.2s
```

---

### 1.3 Combined One-Shot Script: `run_pipeline.py`

Convenience wrapper that runs both phases sequentially.

```bash
python run_pipeline.py \
    --candidates ./India_runs_data_and_ai_challenge/candidates.jsonl \
    --jd ./India_runs_data_and_ai_challenge/job_description.json \
    --out ./submission.csv \
    [--cache-dir ./feature_cache] \
    [--config ./config/ranking_config.yaml]
```

This is the script invoked by the Docker sandbox entrypoint.

---

## 2. JD Input Format

The JD is provided as a structured JSON file (converted from job_description.docx). If no JSON is provided, `rank.py` uses the hardcoded JD text from `config/jd_text.py`.

```json
{
    "title": "Senior AI Engineer — Founding Team",
    "company": "Redrob AI",
    "location": "Pune/Noida, India (Hybrid)",
    "experience_years": {"min": 5, "max": 9},
    "must_have_skills": [
        "embedding-based retrieval",
        "vector database",
        "Python",
        "evaluation frameworks (NDCG, MRR, MAP)"
    ],
    "nice_to_have_skills": [
        "LLM fine-tuning",
        "learning-to-rank",
        "distributed systems"
    ],
    "hard_disqualifiers": [
        "consulting-only career",
        "LLM-only experience <12 months",
        "no production deployment",
        "entire career in pure research"
    ],
    "preferred_locations": ["Pune", "Noida"],
    "acceptable_locations": ["Hyderabad", "Mumbai", "Delhi NCR", "Bangalore", "Chennai"],
    "salary_range_lpa": {"min": 25, "max": 55},
    "full_text": "Senior AI Engineer founding team role requiring production experience..."
}
```

---

## 3. Internal Python Module API

### 3.1 Module: `src.data.reader`

```python
class CandidateStreamReader:
    """Streaming JSONL reader. Yields one parsed candidate dict at a time."""
    
    def __init__(self, filepath: str, skip_invalid: bool = True):
        ...
    
    def __iter__(self) -> Iterator[dict]:
        """Yields valid candidate dicts. Skips invalid lines."""
        ...
    
    def get_stats(self) -> dict:
        """Returns {'total_lines': int, 'valid': int, 'skipped': int, 'errors': list}"""
        ...
```

### 3.2 Module: `src.features.embedding`

```python
class EmbeddingEncoder:
    """Wraps sentence-transformers for batch encoding."""
    
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        ...
    
    def load_model(self) -> None:
        """Load model weights from disk or HuggingFace cache."""
        ...
    
    def encode_batch(self, texts: list[str], normalize: bool = True) -> np.ndarray:
        """
        Encode a batch of texts.
        Returns: np.ndarray shape [len(texts), embedding_dim], dtype=float32
        """
        ...
    
    def encode_single(self, text: str, normalize: bool = True) -> np.ndarray:
        """Encode a single text. Returns: np.ndarray shape [embedding_dim]"""
        ...
    
    @property
    def embedding_dim(self) -> int:
        """Return the embedding dimension (e.g., 384 for MiniLM-L6)."""
        ...
```

### 3.3 Module: `src.features.structured`

```python
class StructuredFeatureExtractor:
    """Extracts all non-embedding features from a candidate dict."""
    
    def __init__(self, config: dict, reference_date: datetime = None):
        ...
    
    def extract(self, candidate: dict) -> dict:
        """
        Extract all structured features for one candidate.
        Returns: feature dict (see DatabaseSchema.md §3.1 for full schema)
        """
        ...
    
    def extract_batch(self, candidates: list[dict]) -> list[dict]:
        """Extract features for a batch of candidates."""
        ...
```

### 3.4 Module: `src.scoring.honeypot`

```python
class HoneypotDetector:
    """Detects honeypot candidates using multiple impossibility flags."""
    
    def check(self, candidate: dict) -> tuple[bool, list[str]]:
        """
        Returns: (is_honeypot: bool, triggered_flags: list[str])
        Flags: 'tenure_impossible', 'expert_zero_duration', 
               'skills_ratio_extreme', 'extreme_title_desc_mismatch'
        """
        ...
    
    def check_tenure_impossible(self, career_history: list) -> bool: ...
    def check_expert_zero_duration(self, skills: list) -> bool: ...
    def check_skills_ratio(self, skills: list, years_exp: float) -> float: ...
    def check_title_desc_mismatch(self, career_history: list) -> int: ...


class HardDisqualifierChecker:
    """Checks hard disqualifier conditions (consulting-only, non-technical)."""
    
    def check(self, candidate: dict) -> tuple[bool, str]:
        """
        Returns: (is_disqualified: bool, reason: str)
        Reasons: '', 'consulting_only', 'non_technical_no_ai'
        """
        ...
```

### 3.5 Module: `src.scoring.dimensions`

```python
class DimensionScorer:
    """Computes all 6 dimension scores for one candidate."""
    
    def __init__(self, config: dict, jd_embedding: np.ndarray):
        ...
    
    def score_all(self, features: dict, candidate_embedding: np.ndarray) -> DimScores:
        """
        Compute all 6 dimension scores.
        Returns: DimScores dataclass with all dimensions.
        """
        ...
    
    def score_semantic_skill_fit(self, features: dict, similarity: float) -> float: ...
    def score_experience_quality(self, features: dict) -> float: ...
    def score_career_progression(self, features: dict) -> float: ...
    def score_behavioral_signals(self, features: dict) -> float: ...
    def score_logistics_fit(self, features: dict) -> float: ...
    def score_profile_integrity(self, features: dict) -> float: ...


@dataclass
class DimScores:
    semantic_skill_fit: float
    experience_quality: float
    career_progression: float
    behavioral_signals: float
    logistics_fit: float
    profile_integrity: float
    disqualifier_multiplier: float
    
    def final_score(self, weights: dict) -> float:
        return (
            weights["semantic_skill_fit"] * self.semantic_skill_fit +
            weights["experience_quality"] * self.experience_quality +
            weights["career_progression"] * self.career_progression +
            weights["behavioral_signals"] * self.behavioral_signals +
            weights["logistics_fit"] * self.logistics_fit +
            weights["profile_integrity"] * self.profile_integrity
        ) * self.disqualifier_multiplier
```

### 3.6 Module: `src.ranking.reasoning`

```python
class ReasoningGenerator:
    """Generates grounded reasoning strings from actual candidate data."""
    
    def __init__(self, templates_path: str = "config/reasoning_templates.yaml"):
        ...
    
    def generate(self, candidate: dict, scores: DimScores, rank: int) -> str:
        """
        Generate a reasoning string for one candidate.
        
        - Uses only facts from the actual candidate dict
        - No hallucination of skills, employers, or credentials
        - Varies template by rank tier: top-10, 11-50, 51-100
        - Returns string ≤300 chars
        """
        ...
    
    def extract_facts(self, candidate: dict, scores: DimScores) -> dict:
        """
        Extract key facts from candidate for template filling.
        Returns: {yoe, title, top_skills, location, notice_days, 
                  response_rate, main_strength, main_gap, ...}
        """
        ...
```

### 3.7 Module: `src.output.writer`

```python
class SubmissionWriter:
    """Writes and validates the final submission CSV."""
    
    def write(
        self,
        ranked_candidates: list[tuple[str, int, float, str]],  # (id, rank, score, reasoning)
        output_path: str
    ) -> None:
        """Write CSV. Raises ValueError if validation fails."""
        ...
    
    def validate(self, output_path: str) -> list[str]:
        """
        Validate the written CSV against submission spec.
        Returns: list of validation errors (empty = valid)
        Checks:
        - Exactly 100 rows
        - Ranks 1-100 each exactly once
        - Scores monotonically non-increasing
        - All candidate_ids valid format CAND_XXXXXXX
        - CSV encoding is UTF-8
        """
        ...
```

---

## 4. Configuration Schema (YAML)

```yaml
# config/ranking_config.yaml

# ─── Model Configuration ────────────────────────────────────────────────────
model:
  embedding_model: "sentence-transformers/all-MiniLM-L6-v2"  # or "BAAI/bge-small-en-v1.5"
  batch_size: 512
  max_text_chars: 4096       # Truncate candidate text before embedding
  normalize_embeddings: true

# ─── Dimension Weights ──────────────────────────────────────────────────────
weights:
  semantic_skill_fit: 0.30
  experience_quality: 0.25
  career_progression: 0.15
  behavioral_signals: 0.15
  logistics_fit: 0.10
  profile_integrity: 0.05

# ─── Sub-dimension Weights ──────────────────────────────────────────────────
semantic_weights:
  semantic_similarity: 0.40
  skill_depth: 0.35
  core_coverage: 0.15
  assessment_boost: 0.10

experience_weights:
  years_exp: 0.30
  product_company: 0.35
  production_evidence: 0.25
  tenure_stability: 0.10

behavioral_weights:
  hiring_readiness: 0.30
  recruiter_engagement: 0.25
  platform_trust: 0.20
  github_activity: 0.15
  market_validation: 0.10

logistics_weights:
  location_fit: 0.50
  notice_period: 0.30
  salary_alignment: 0.20

# ─── JD Parameters ──────────────────────────────────────────────────────────
jd:
  target_yoe_min: 5
  target_yoe_max: 9
  sweet_spot_yoe_min: 6
  sweet_spot_yoe_max: 8
  salary_target_min_lpa: 25.0
  salary_target_max_lpa: 55.0
  preferred_locations:
    - "Pune"
    - "Noida"
  acceptable_locations:
    - "Hyderabad"
    - "Mumbai"
    - "Delhi"
    - "Gurgaon"
    - "Gurugram"
    - "Bangalore"
    - "Bengaluru"
    - "Chennai"
    - "Kolkata"

# ─── Notice Period Scoring ───────────────────────────────────────────────────
notice_period_tiers:
  - {max_days: 30,  score: 1.0}
  - {max_days: 60,  score: 0.7}
  - {max_days: 90,  score: 0.5}
  - {max_days: 180, score: 0.3}

# ─── Hard Disqualifier Configuration ───────────────────────────────────────
disqualifiers:
  consulting_firms:
    - "tcs"
    - "tata consultancy services"
    - "wipro"
    - "infosys"
    - "accenture"
    - "cognizant"
    - "capgemini"
    - "hcl technologies"
    - "hcl"
    - "tech mahindra"
    - "mphasis"
    - "hexaware"
    - "niit technologies"
    - "ltimindtree"
    - "mindtree"
  non_technical_titles:
    - "accountant"
    - "graphic designer"
    - "content writer"
    - "civil engineer"
    - "mechanical engineer"
    - "hr manager"
    - "customer support"
    - "sales executive"
    - "marketing manager"

# ─── Honeypot Detection Thresholds ─────────────────────────────────────────
honeypot:
  skills_ratio_definitive: 2.0   # expert+advanced per year → definitive honeypot
  skills_ratio_suspicion: 1.5    # → suspicion weight
  title_desc_mismatch_definitive: 3   # mismatched roles → definitive honeypot
  title_desc_mismatch_suspicion: 2    # → suspicion weight
  tenure_tolerance_months: 12    # Tolerance for tenure_impossible check

# ─── Path Configuration ─────────────────────────────────────────────────────
paths:
  feature_cache_dir: "./feature_cache"
  submission_output: "./submission.csv"
  audit_log: "./ranking_audit.json"
  jd_text_module: "config.jd_text"

# ─── Performance ────────────────────────────────────────────────────────────
performance:
  max_runtime_seconds: 270       # Target; leave 30s buffer under 300s limit
  target_candidates: 100
  reference_date: "2025-06-01"   # Dataset reference date for recency calculations
```

---

## 5. Output Schema

### 5.1 Submission CSV

```
candidate_id,rank,score,reasoning
CAND_0001234,1,0.8934,"7.2 yrs; ML Engineer at FinTech startup; FAISS + sentence-transformer production deployment cited in descriptions; Pune; 15d notice; response rate 82%."
```

**Column constraints**:

| Column | Type | Format | Constraint |
|---|---|---|---|
| candidate_id | string | CAND_XXXXXXX | Must exist in candidates.jsonl |
| rank | integer | 1–100 | Unique, sequential |
| score | float | 4 decimal places | Monotonically non-increasing |
| reasoning | string | UTF-8 text | ≤300 chars; no embedded newlines; CSV-escaped quotes |

### 5.2 Reasoning Content Rules

The reasoning column must:
1. Name the candidate's current title or most recent relevant title
2. Name years of experience (from profile or derived)
3. Reference at least one specific JD-relevant skill or technology found in the candidate's actual data
4. Reference one logistics fact (location, notice period)
5. For ranks 11–100: acknowledge at least one concern or gap
6. Not reference any skill, employer, or credential absent from the candidate's profile

**Regex pattern for basic validation**:
```python
import re
REASONING_PATTERN = re.compile(r".{20,300}")  # At least 20 chars, at most 300
```

### 5.3 Audit Log JSON (optional)

```json
{
    "run_timestamp": "ISO-8601",
    "phase1_runtime_seconds": 187,
    "phase2_runtime_seconds": 43,
    "total_runtime_seconds": 230,
    "total_candidates_read": 100000,
    "valid_candidates": 99847,
    "honeypots_detected": 82,
    "consulting_only_disqualified": 7841,
    "non_technical_disqualified": 612,
    "total_disqualified": 8535,
    "candidates_scored": 91312,
    "model_used": "sentence-transformers/all-MiniLM-L6-v2",
    "weights_used": {...},
    "score_stats": {
        "mean": 0.2341,
        "std": 0.1823,
        "min": 0.0000,
        "max": 0.9134,
        "top_100_min": 0.4123
    },
    "top_100": [
        {
            "rank": 1,
            "candidate_id": "CAND_0001234",
            "final_score": 0.8934,
            "dim_scores": {
                "semantic_skill_fit": 0.92,
                "experience_quality": 0.88,
                "career_progression": 0.75,
                "behavioral_signals": 0.82,
                "logistics_fit": 1.00,
                "profile_integrity": 0.90
            },
            "reasoning": "7.2 yrs; ML Engineer at FinTech startup..."
        }
    ]
}
```

---

## 6. Project Directory Structure

```
INDIA-RUN-RESUME-ANALYZER/
├── India_runs_data_and_ai_challenge/     # Raw dataset (read-only)
│   ├── candidates.jsonl
│   ├── candidate_schema.json
│   ├── sample_candidates.json
│   ├── sample_submission.csv
│   ├── job_description.docx
│   ├── redrob_signals_doc.docx
│   ├── submission_spec.docx
│   └── README.docx
│
├── src/
│   ├── __init__.py
│   ├── data/
│   │   ├── __init__.py
│   │   ├── reader.py
│   │   └── validator.py
│   ├── features/
│   │   ├── __init__.py
│   │   ├── text_builder.py
│   │   ├── embedding.py
│   │   ├── structured.py
│   │   └── cache.py
│   ├── scoring/
│   │   ├── __init__.py
│   │   ├── honeypot.py
│   │   ├── dim_semantic.py
│   │   ├── dim_experience.py
│   │   ├── dim_progression.py
│   │   ├── dim_behavioral.py
│   │   ├── dim_logistics.py
│   │   └── dim_integrity.py
│   ├── ranking/
│   │   ├── __init__.py
│   │   ├── assembler.py
│   │   ├── selector.py
│   │   └── reasoning.py
│   └── output/
│       ├── __init__.py
│       └── writer.py
│
├── config/
│   ├── ranking_config.yaml
│   ├── jd_text.py                         # Hardcoded JD text for embedding
│   └── reasoning_templates.yaml
│
├── tests/
│   ├── test_reader.py
│   ├── test_honeypot.py
│   ├── test_dimensions.py
│   ├── test_reasoning.py
│   └── test_output_validation.py
│
├── feature_cache/                          # Generated by precompute.py (gitignored)
│
├── docs/                                   # This documentation
│   ├── DatasetAnalysis.md
│   ├── PRD.md
│   ├── Architecture.md
│   ├── DatabaseSchema.md
│   ├── RankingLogic.md
│   ├── API_Spec.md
│   └── Tasks.md
│
├── precompute.py                           # Phase 1 entry point
├── rank.py                                 # Phase 2 entry point
├── run_pipeline.py                         # Combined entry point
├── requirements.txt
├── Dockerfile
└── README.md
```
