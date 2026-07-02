# System Architecture — AI Recruiter Copilot

**Version**: 1.0  
**Constraint Summary**: CPU-only, ≤5 min, ≤16 GB RAM, offline, no external APIs

---

## 1. High-Level Architecture

The system is split into two phases to meet the 5-minute runtime constraint:

```
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 1: OFFLINE PRE-COMPUTATION  (precompute.py)              │
│                                                                   │
│  candidates.jsonl → [Stream Reader] → [Feature Extractor]        │
│                                     → [Embedding Encoder]        │
│                                     → [Structured Scorer]        │
│                                     → [Feature Cache] (disk)     │
│                                                                   │
│  Output: feature_cache/ directory (~2-4 GB)                      │
│  Runtime: ~3-4 minutes (one-time, run before contest deadline)   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  PHASE 2: FAST ONLINE RANKING  (rank.py)                         │
│                                                                   │
│  feature_cache/ → [Score Assembler] → [Top-100 Selector]         │
│  jd_embedding   → [Similarity Computer]                          │
│                 → [Hard Disqualifier Filter]                      │
│                 → [Weighted Score Combiner]                       │
│                 → [Reasoning Generator]                           │
│                 → submission.csv                                  │
│                                                                   │
│  Runtime: ≤90 seconds                                            │
└─────────────────────────────────────────────────────────────────┘
```

**Total target runtime**: Phase 1 (~180s) + Phase 2 (~90s) = ~270s < 300s limit.

In the Docker sandbox evaluation, both phases run sequentially within the 5-minute window. The Docker entrypoint calls `precompute.py` then `rank.py`.

---

## 2. Component Architecture

### 2.1 Phase 1 Components

```
src/
├── data/
│   ├── reader.py          # Streaming JSONL reader
│   └── validator.py       # Schema validation + skip logic
│
├── features/
│   ├── text_builder.py    # Builds text corpus per candidate for embedding
│   ├── embedding.py       # Local model loading + batch encoding
│   ├── structured.py      # All non-embedding feature extraction
│   └── cache.py           # Read/write numpy arrays + metadata to disk
│
├── scoring/
│   ├── honeypot.py        # Honeypot and hard disqualifier detection
│   ├── dim_semantic.py    # Dimension 1: Semantic & Skill Fit (partial, offline)
│   ├── dim_experience.py  # Dimension 2: Experience Quality
│   ├── dim_progression.py # Dimension 3: Career Progression
│   ├── dim_behavioral.py  # Dimension 4: Behavioral Signals
│   ├── dim_logistics.py   # Dimension 5: Location & Logistics
│   └── dim_integrity.py   # Dimension 6: Profile Integrity
│
└── precompute.py          # Orchestrator for Phase 1
```

### 2.2 Phase 2 Components

```
src/
├── ranking/
│   ├── assembler.py       # Load cached features, compute final scores
│   ├── selector.py        # Top-100 selection with tie-breaking
│   └── reasoning.py       # Reasoning string generation
│
├── output/
│   └── writer.py          # CSV writer with validation
│
└── rank.py                # Orchestrator for Phase 2
```

---

## 3. Data Flow

```
candidates.jsonl (line-by-line)
    │
    ▼
StreamReader.read() — yields parsed dicts one at a time
    │
    ▼
Validator.validate() — check required fields, skip invalid
    │
    ├─► DisqualifierChecker.check()
    │       │
    │       ├── is_consulting_only → disqualify_flag = True
    │       ├── is_non_technical_no_ai → disqualify_flag = True  
    │       └── is_honeypot → disqualify_flag = True
    │       
    │   (disqualified candidates: store disqualify_flag, skip expensive scoring)
    │
    ├─► TextBuilder.build_candidate_text()
    │       Concatenates: headline + summary + career descriptions + skill names
    │       Output: single string per candidate for embedding
    │
    ├─► StructuredFeatureExtractor.extract()
    │       Output: dict of scalar features per candidate
    │       (years_exp, product_ratio, notice_days, response_rate, ...)
    │
    └─► Accumulate into batch of 512
            │
            ▼
        EmbeddingEncoder.encode_batch(texts)
            Uses: all-MiniLM-L6-v2 (local, loaded once)
            Output: numpy array [512, 384]
            │
            ▼
        Cache.save_batch(embeddings, structured_features)
            Writes to: feature_cache/embeddings_{batch_id}.npy
                       feature_cache/structured_{batch_id}.pkl


Phase 2:

JD text → EmbeddingEncoder.encode([jd_text]) → jd_embedding [1, 384]

for each candidate_batch in cache:
    candidate_embeddings [B, 384]
    cosine_similarity = candidate_embeddings @ jd_embedding.T / (norms)
    → semantic_sim scores [B]
    
    structured_features → dim_scorers → [semantic_fit, exp_quality, ...]
    
    final_score = weighted_combination(all_dims) * disqualifier_multiplier

top_100 = argsort(final_scores)[-100:][::-1]
reasoning = ReasoningGenerator.generate(top_100_candidates, scores)
CSVWriter.write(top_100, scores, reasoning) → submission.csv
```

---

## 4. Technology Stack

| Component | Choice | Rationale |
|---|---|---|
| Language | Python 3.10+ | Required by challenge; best ecosystem for ML |
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2` | 23MB, 384-dim, fast CPU inference, strong quality |
| Fallback model | `BAAI/bge-small-en-v1.5` | 24MB, 384-dim, strong retrieval-focused embeddings |
| Similarity compute | `numpy` cosine similarity | Vectorized, CPU-efficient, no GPU required |
| JSONL reading | Built-in `json` + `open()` streaming | No pandas needed for ingestion; memory-safe |
| Feature storage | `numpy` `.npy` + `pickle` for structs | Fast I/O, compact, no DB needed |
| Parallelization | `concurrent.futures.ProcessPoolExecutor` | True parallelism on multiple CPU cores |
| Structured scoring | Pure Python + `numpy` | No heavy dependencies |
| CSV output | `csv` module (stdlib) | No pandas overhead at output stage |
| Config | `PyYAML` | Human-editable config file |
| Testing | `pytest` | Standard Python testing |

**Deliberately excluded**:
- `pandas`: Memory overhead for 100K records; streaming approach is preferred
- `transformers` directly: `sentence-transformers` provides cleaner batch API
- Any GPU libraries: CUDA unavailable in sandbox
- Any external API clients: OpenAI, Anthropic, etc. banned during ranking

---

## 5. Parallelization Strategy

### 5.1 Embedding Batch Parallelism

`sentence-transformers` batch encode uses all available CPU cores automatically via PyTorch's thread pool. Set `OMP_NUM_THREADS` appropriately. Batch size of 512 is optimal for CPU memory/throughput tradeoff.

### 5.2 Structured Feature Extraction

For the 100K candidates, structured feature extraction (no embedding) runs at ~50K candidates/second. Use a single-process loop — parallelization overhead isn't worth it at this speed.

### 5.3 Score Assembly (Phase 2)

All dimension scores can be computed in vectorized numpy operations:
```python
# All operations are O(N) numpy array math — no Python loops needed
final_scores = (
    0.30 * semantic_fit_scores +     # [N] numpy array
    0.25 * experience_scores +
    0.15 * progression_scores +
    0.15 * behavioral_scores +
    0.10 * logistics_scores +
    0.05 * integrity_scores
) * disqualifier_multipliers          # [N] binary array
```

This processes all 100K candidates in <1 second.

### 5.4 Reasoning Generation

The top 100 reasoning strings are generated sequentially (no parallelism needed) at ~5ms per candidate = 500ms total.

---

## 6. Memory Budget

| Component | Peak Memory |
|---|---|
| Embedding model (MiniLM-L6) | ~300 MB |
| Batch of 512 candidate texts (pre-encode) | ~50 MB |
| Batch embeddings [512, 384] float32 | ~0.8 MB |
| Full embeddings on disk, loaded in chunks | ~150 MB per batch of 10K |
| Structured features for 100K candidates | ~800 MB (all in memory) |
| Phase 2 score arrays [100K, 6 dims] | ~5 MB |
| **Total peak** | **~1.5 GB** |

This is well within the 16 GB limit, with ample headroom for the OS and Python interpreter.

---

## 7. Disk Budget

| Artifact | Size (est.) |
|---|---|
| Embeddings: 100K × 384 float32 | ~154 MB |
| Structured features: 100K × ~50 scalar features | ~200 MB |
| Honeypot flags: 100K boolean | ~1 MB |
| Disqualifier flags: 100K boolean | ~1 MB |
| **Total feature cache** | **~360 MB** |

Well within the 5 GB limit. The model weights (~300 MB) are loaded at runtime, not stored in the cache.

---

## 8. Anti-Honeypot Architecture

The honeypot detection is a **pre-scoring guard** that runs before any expensive computation:

```
read candidate
    │
    ▼
HoneypotDetector.check(candidate)
    ├── check_tenure_impossible()    O(R) where R = career_history length
    ├── check_expert_zero_duration() O(S) where S = skills length
    ├── check_skills_experience_ratio()
    └── check_extreme_title_desc_mismatch()
    │
    ▼
is_honeypot → disqualify_multiplier = 0.0
              skip embedding encoding
              set all dimension scores = 0.0
              store candidate_id with flag for audit log
```

This ensures honeypot candidates never enter the expensive semantic scoring pipeline.

---

## 9. Reasoning Generation Architecture

Reasoning is generated via **template filling from actual candidate data** — no LLM calls, no hallucination risk.

```python
def generate_reasoning(candidate: dict, scores: DimScores, rank: int) -> str:
    """
    Build a grounded reasoning string from actual candidate fields.
    Templates vary by rank tier: top-10, 11-50, 51-100.
    """
    facts = extract_facts(candidate)
    # facts = {yoe, title, top_skills, location, notice_days, response_rate,
    #          production_evidence, main_strength, main_gap}
    
    if rank <= 10:
        template = TOP_10_TEMPLATES[hash(candidate_id) % len(TOP_10_TEMPLATES)]
    elif rank <= 50:
        template = MID_RANGE_TEMPLATES[hash(candidate_id) % len(MID_RANGE_TEMPLATES)]
    else:
        template = LOWER_RANGE_TEMPLATES[hash(candidate_id) % len(LOWER_RANGE_TEMPLATES)]
    
    return template.format(**facts)[:300]
```

Multiple template variants per tier ensure variation. Hash-based template selection is deterministic (same candidate always gets same template) but looks organic across the 100 rows.

---

## 10. Configuration Architecture

All tuneable parameters live in `config/ranking_config.yaml`:

```yaml
model:
  embedding_model: "sentence-transformers/all-MiniLM-L6-v2"
  batch_size: 512
  
weights:
  semantic_skill_fit: 0.30
  experience_quality: 0.25
  career_progression: 0.15
  behavioral_signals: 0.15
  logistics_fit: 0.10
  profile_integrity: 0.05

jd:
  target_yoe_min: 5
  target_yoe_max: 9
  sweet_spot_yoe_min: 6
  sweet_spot_yoe_max: 8
  salary_target_min_lpa: 25
  salary_target_max_lpa: 55
  preferred_locations: ["Pune", "Noida"]
  acceptable_locations: ["Hyderabad", "Mumbai", "Delhi", "Gurgaon", "Bangalore", "Chennai"]

scoring:
  notice_period_tiers:
    - {max_days: 30,  score: 1.0}
    - {max_days: 60,  score: 0.7}
    - {max_days: 90,  score: 0.5}
    - {max_days: 180, score: 0.3}

paths:
  feature_cache_dir: "./feature_cache"
  submission_output: "./submission.csv"
```

---

## 11. Docker Sandbox Entrypoint

```dockerfile
# Dockerfile (for reference — not to be implemented, just architecture)
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Pre-download model weights during build (they count as disk, not network during ranking)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
COPY . .
CMD ["bash", "-c", "python precompute.py --candidates ./candidates.jsonl && python rank.py --jd ./job_description.json --out ./submission.csv"]
```

**Critical**: The embedding model weights must be baked into the Docker image or downloaded during `precompute.py` before the 5-minute clock starts. Check contest rules on whether Docker build time counts toward the 5-minute budget.
