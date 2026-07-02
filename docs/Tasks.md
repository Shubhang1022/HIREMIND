# Tasks — Implementation Plan

**Project**: India Run AI & Data Challenge — AI Recruiter Copilot  
**Version**: 1.0  
**Status**: Awaiting Approval  
**Total Estimated Effort**: ~8–12 days for solo developer with ML background

> ⚠️ **Do NOT begin implementation until this task list is approved.**

---

## Phase 0: Setup & Scaffolding

**Goal**: Create project structure, install dependencies, verify everything runs in a clean environment.

---

### Task 0.1 — Initialize Project Structure

**Description**: Create the full directory tree from API_Spec.md §6. Create `__init__.py` files for all packages. Create placeholder `.py` files with module-level docstrings.

**Acceptance Criteria**:
- All directories exist: `src/data/`, `src/features/`, `src/scoring/`, `src/ranking/`, `src/output/`, `config/`, `tests/`, `docs/`, `feature_cache/` (gitignored)
- All `__init__.py` files created
- `python -c "from src.data import reader"` runs without error
- `feature_cache/` is in `.gitignore`

**Complexity**: S  
**Dependencies**: None

---

### Task 0.2 — Create requirements.txt

**Description**: Pin all dependencies with exact versions. Verify CPU-only torch install.

**Acceptance Criteria**:
- `requirements.txt` includes: `sentence-transformers==2.7.0`, `numpy==1.26.4`, `torch==2.2.2+cpu` (CPU-only build), `pyyaml==6.0.1`, `pytest==8.1.0`, `tqdm==4.66.2`
- `pip install -r requirements.txt` completes without error on a machine without CUDA
- `python -c "import torch; print(torch.cuda.is_available())"` prints `False` (confirms CPU-only install)
- Total install size < 2 GB

**Complexity**: S  
**Dependencies**: Task 0.1

---

### Task 0.3 — Create Configuration Files

**Description**: Create `config/ranking_config.yaml` (full config from API_Spec.md §4), `config/jd_text.py` (hardcoded JD text for embedding), and `config/reasoning_templates.yaml` (reasoning string templates by rank tier).

**Acceptance Criteria**:
- `ranking_config.yaml` validates against the schema in API_Spec.md §4 (all keys present, types correct)
- `config/jd_text.py` exports a `JD_TEXT` string constant (≥200 chars, rich with JD semantics)
- `reasoning_templates.yaml` contains ≥3 templates for each tier: top-10, mid-range (11–50), lower-range (51–100)
- `from config.jd_text import JD_TEXT` works
- Config loads cleanly: `import yaml; yaml.safe_load(open('config/ranking_config.yaml'))`

**Complexity**: S  
**Dependencies**: Task 0.1

---

### Task 0.4 — Verify Dataset Access

**Description**: Write a quick script `scripts/verify_dataset.py` that confirms the dataset is readable and the first 10 candidates can be parsed.

**Acceptance Criteria**:
- Script reads first 10 lines of `candidates.jsonl` and prints `candidate_id` for each
- Script reports file size
- Script exits 0 if all 10 parse correctly, 1 if any fail
- Script runtime < 5 seconds

**Complexity**: S  
**Dependencies**: Task 0.1

---

## Phase 1: Data Pipeline

**Goal**: Implement streaming JSONL reader, schema validator, and candidate text builder.

---

### Task 1.1 — Implement Streaming JSONL Reader (`src/data/reader.py`)

**Description**: Implement `CandidateStreamReader` class from API_Spec.md §3.1. Must stream line-by-line; must never load all 100K candidates into memory simultaneously.

**Acceptance Criteria**:
- `for candidate in reader: ...` yields one dict at a time
- Can process all 100K candidates while peak memory stays below 500 MB (measured with `tracemalloc`)
- Handles malformed JSON lines gracefully: logs warning, continues iteration
- `reader.get_stats()` returns correct counts after exhaustion
- Reader with `candidates.jsonl` as input: `assert sum(1 for _ in reader) == 100000` (or valid count)
- Unit test in `tests/test_reader.py`: test empty file, single valid line, single invalid line, mixed

**Complexity**: S  
**Dependencies**: Task 0.1

---

### Task 1.2 — Implement Schema Validator (`src/data/validator.py`)

**Description**: Implement field presence and type validation for candidate dicts. Validates required fields only (performance-sensitive; don't do full JSON Schema validation for every candidate in the hot path).

**Acceptance Criteria**:
- `validate(candidate)` returns `(True, [])` for valid candidates
- `validate(candidate)` returns `(False, [list_of_missing_fields])` for invalid candidates
- Handles missing top-level keys: `profile`, `career_history`, `education`, `skills`, `redrob_signals`, `candidate_id`
- Handles candidate_id format validation (regex `^CAND_[0-9]{7}$`)
- Validates that `career_history` is a non-empty list
- Processing 100K validations takes < 5 seconds
- Unit tests for: valid candidate, missing profile, missing career_history, invalid candidate_id format

**Complexity**: S  
**Dependencies**: Task 1.1

---

### Task 1.3 — Implement Candidate Text Builder (`src/features/text_builder.py`)

**Description**: Implement `build_candidate_text(candidate)` and `build_jd_text()` functions. The candidate text is used as input to the embedding model.

**Acceptance Criteria**:
- `build_candidate_text(candidate)` returns a string ≤4096 chars
- Output includes: headline, summary, career descriptions (most recent 5 roles, most recent first), skill names
- Current role descriptions are prefixed with "Currently: " for semantic emphasis
- Output is a single flat string (no newlines; use space separator)
- `build_jd_text()` returns the full JD embedding text from `config/jd_text.py` (≥200 chars)
- For a candidate with empty career_history descriptions, function returns non-empty string (falls back to headline + skills)
- Unit tests for: full candidate, candidate with empty summary, candidate with no skills

**Complexity**: S  
**Dependencies**: Task 0.3, Task 1.2

---

### Task 1.4 — Implement Feature Cache (`src/features/cache.py`)

**Description**: Implement disk read/write for embedding batches (numpy .npy) and structured feature batches (pickle). Implement `meta.json` index.

**Acceptance Criteria**:
- `save_embedding_batch(batch_id, embeddings)` writes to `feature_cache/embeddings/batch_{id:03d}.npy`
- `load_embedding_batch(batch_id)` reads back and returns numpy array with matching shape
- `save_structured_batch(batch_id, features)` writes to `feature_cache/structured/batch_{id:03d}.pkl`
- `load_structured_batch(batch_id)` reads back list of dicts
- `save_meta(meta_dict)` writes `feature_cache/meta.json`
- `load_meta()` reads and returns meta dict
- Round-trip test: save 512 random embeddings → load → assert arrays are equal (np.allclose)
- Round-trip test: save list of 10 feature dicts → load → assert equal

**Complexity**: S  
**Dependencies**: Task 0.1

---

## Phase 2: Semantic Layer

**Goal**: Load the local embedding model, encode the JD, encode all candidates in batches, cache embeddings.

---

### Task 2.1 — Implement Embedding Encoder (`src/features/embedding.py`)

**Description**: Implement `EmbeddingEncoder` class wrapping `sentence_transformers.SentenceTransformer`. Must use CPU-only; must batch encode; must normalize embeddings.

**Acceptance Criteria**:
- `encoder = EmbeddingEncoder()` loads model without CUDA
- `encoder.encode_batch(texts)` returns numpy array of shape `[len(texts), 384]` for MiniLM-L6
- Embeddings are L2-normalized (each row has norm ≈ 1.0 within 1e-6 tolerance)
- `encoder.encode_batch(["test"] * 512)` completes in < 30 seconds on a 4-core CPU
- `encoder.embedding_dim == 384` for default model
- Model loads from local HuggingFace cache (no network call during inference test)
- Unit test: encode two semantically identical sentences → cosine similarity > 0.95
- Unit test: encode two semantically unrelated sentences → cosine similarity < 0.5

**Complexity**: M  
**Dependencies**: Task 0.2

---

### Task 2.2 — Implement Structured Feature Extractor (`src/features/structured.py`)

**Description**: Implement `StructuredFeatureExtractor` that extracts all ~50 structured features from a candidate dict as listed in DatabaseSchema.md §3.1.

**Acceptance Criteria**:
- `extractor.extract(candidate)` returns a dict with all keys listed in DatabaseSchema.md §3.1
- All numeric values are Python floats or ints (no numpy scalars that break pickle)
- Handles missing optional fields gracefully (certifications=None, languages=None, duration_months=None)
- Correctly computes `product_company_ratio` for a consulting-heavy candidate (value near 0)
- Correctly computes `days_since_active` from `last_active_date` and reference_date
- Correctly computes `derived_years_exp` from sum of `duration_months`
- Processing 1000 candidates in < 1 second
- Unit tests for: AI/ML engineer (well-featured), non-technical candidate, candidate with missing optional fields, consulting-only candidate

**Complexity**: L  
**Dependencies**: Task 0.3, Task 1.2

---

### Task 2.3 — Implement Pre-computation Orchestrator (`precompute.py`)

**Description**: Wire together reader → validator → text_builder → embedding encoder → structured extractor → cache writer. Process all 100K candidates in batches of 512. Write progress to stdout.

**Acceptance Criteria**:
- `python precompute.py --candidates <path> --cache-dir ./feature_cache --verbose` runs end-to-end
- Produces `feature_cache/meta.json` with correct total_candidates count
- Produces embedding batch files (`embeddings/batch_*.npy`)
- Produces structured batch files (`structured/batch_*.pkl`)
- Peak memory during run < 4 GB (measured with `tracemalloc` or `psutil`)
- Total runtime for 100K candidates < 240 seconds on a 4-core CPU
- Script handles keyboard interrupt gracefully (partial cache is usable for debugging)
- Exit code 0 on success

**Complexity**: M  
**Dependencies**: Task 1.1, Task 1.3, Task 1.4, Task 2.1, Task 2.2

---

## Phase 3: Structured Scoring

**Goal**: Implement all 6 dimension scorers with sub-scoring formulas from RankingLogic.md.

---

### Task 3.1 — Implement Dimension 1: Semantic & Skill Fit (`src/scoring/dim_semantic.py`)

**Description**: Implement `score_semantic_skill_fit(features, similarity_score)` per RankingLogic.md §3.

**Acceptance Criteria**:
- Returns float in [0.0, 1.0]
- `skill_depth_score` correctly weights proficiency × duration × endorsements per formula
- `keyword_stuffing_penalty` = 0.4 for a candidate with 7 AI buzzwords in skills and no production evidence
- `keyword_stuffing_penalty` = 1.0 for a candidate with 7 AI buzzwords AND production evidence in descriptions
- `llm_recency_penalty` = 0.7 for a candidate whose AI work all started within 12 months
- `core_coverage_score` returns 1.0 for a candidate with all 4 must-have skill clusters
- Final score for a strong AI engineer with similarity 0.85 > 0.7
- Final score for a non-AI candidate with similarity 0.1 < 0.2

**Complexity**: L  
**Dependencies**: Task 2.2

---

### Task 3.2 — Implement Dimension 2: Experience Quality (`src/scoring/dim_experience.py`)

**Description**: Implement `score_experience_quality(features)` per RankingLogic.md §4.

**Acceptance Criteria**:
- Returns float in [0.0, 1.0]
- `score_years_exp(7.0)` ≈ 1.0 (sweet spot)
- `score_years_exp(2.0)` < 0.5
- `score_years_exp(15.0)` < `score_years_exp(7.0)` (tapering)
- `product_company_ratio = 0.95` → score > 0.85
- `product_company_ratio = 0.1` → score < 0.35 (before hard disqualifier)
- `production_evidence_score` > 0.5 for description containing "deployed to production A/B test latency SLA"
- `job_hop_penalty = 0.70` for candidate with 5 short stints in 6 years
- Unit tests for each sub-function, plus integration test with full candidate feature dict

**Complexity**: M  
**Dependencies**: Task 2.2

---

### Task 3.3 — Implement Dimension 3: Career Progression (`src/scoring/dim_progression.py`)

**Description**: Implement `score_career_progression(features)` per RankingLogic.md §5.

**Acceptance Criteria**:
- Returns float in [0.0, 1.0]
- `infer_seniority("Senior ML Engineer")` == 3
- `infer_seniority("Principal AI Architect")` == 5
- `infer_seniority("ML Engineer")` == 2 (no modifier → mid)
- Seniority trajectory bonus +0.2 for monotonically increasing levels: [1, 2, 3, 4]
- No bonus for: [3, 2, 3, 4] (non-monotonic)
- `score_leadership_evidence` > 0.5 for description mentioning "led a team of 5 engineers and architected the system"
- Stagnation penalty −0.1 for same title held for 60+ months

**Complexity**: M  
**Dependencies**: Task 2.2

---

### Task 3.4 — Implement Dimension 4: Behavioral Signals (`src/scoring/dim_behavioral.py`)

**Description**: Implement `score_behavioral_signals(features)` per RankingLogic.md §6.

**Acceptance Criteria**:
- Returns float in [0.0, 1.0]
- `open_to_work=True, days_since_active=5, notice_period_days=15` → hiring_readiness > 0.9
- `open_to_work=False, days_since_active=200, notice_period_days=90` → hiring_readiness < 0.4
- `recruiter_response_rate=0.9, avg_response_time_hours=2` → engagement_score > 0.85
- `recruiter_response_rate=0.1, avg_response_time_hours=200` → engagement_score < 0.25
- `github_activity_score=-1` → treated as neutral (not zero), weight reduced to 0.05
- `saved_by_recruiters_30d=10` → market_validation_score ≈ 0.77 (log scale)

**Complexity**: S  
**Dependencies**: Task 2.2

---

### Task 3.5 — Implement Dimension 5: Logistics Fit (`src/scoring/dim_logistics.py`)

**Description**: Implement `score_logistics_fit(features)` per RankingLogic.md §7.

**Acceptance Criteria**:
- Returns float in [0.0, 1.0]
- Location "Pune, Maharashtra" → location_fit_score = 1.0
- Location "Toronto" + willing_to_relocate=True → location_fit_score = 0.4
- Location "Toronto" + willing_to_relocate=False → location_fit_score = 0.2
- Location "Hyderabad" → location_fit_score = 0.85
- notice_period_days=15 → notice_score = 1.0
- notice_period_days=75 → notice_score = 0.5
- Salary range [30, 50] LPA → full overlap with [25, 55] → salary_alignment = 1.0
- Salary range [60, 80] LPA → no overlap → salary_alignment ≤ 0.3

**Complexity**: S  
**Dependencies**: Task 2.2

---

### Task 3.6 — Implement Dimension 6: Profile Integrity (`src/scoring/dim_integrity.py`)

**Description**: Implement `score_profile_integrity(features)` per RankingLogic.md §8.

**Acceptance Criteria**:
- Returns float in [0.0, 1.0]
- profile_completeness_score=95, verified_email=True, verified_phone=True, linkedin=True → score > 0.9
- profile_completeness_score=30 → penalty applied → score < 0.5
- Consistency check: stated 7.0 yrs, derived 6.5 yrs → consistency_score = 1.0 (within tolerance)
- Consistency check: stated 15.0 yrs, derived 6.0 yrs → consistency_score < 0.5

**Complexity**: S  
**Dependencies**: Task 2.2

---

## Phase 4: Honeypot Detection

**Goal**: Implement honeypot detector and hard disqualifier checker with all flags from RankingLogic.md §2.

---

### Task 4.1 — Implement Honeypot Detector (`src/scoring/honeypot.py`)

**Description**: Implement `HoneypotDetector` and `HardDisqualifierChecker` classes per RankingLogic.md §2 and API_Spec.md §3.4.

**Acceptance Criteria**:
- `HoneypotDetector.check(candidate)` returns `(True, ["expert_zero_duration"])` for a candidate with `proficiency="expert", duration_months=0`
- `HoneypotDetector.check(candidate)` returns `(True, ["tenure_impossible"])` for a candidate who worked 96 months at a company that started 60 months ago
- `HoneypotDetector.check(candidate)` returns `(False, [])` for a clean candidate
- `HardDisqualifierChecker.check(candidate)` returns `(True, "consulting_only")` for a candidate with only TCS/Infosys/Wipro roles
- `HardDisqualifierChecker.check(candidate)` returns `(True, "non_technical_no_ai")` for a candidate titled "Accountant" with no AI career history
- `HardDisqualifierChecker.check(candidate)` returns `(False, "")` for a valid ML Engineer
- False positive test: a legitimate "Marketing Manager" who pivoted to ML (has ML career history) → NOT disqualified
- Performance: checking 100K candidates in < 10 seconds

**Complexity**: M  
**Dependencies**: Task 1.2

---

### Task 4.2 — Write Honeypot Flag File

**Description**: Integrate honeypot and disqualifier checks into `precompute.py`. Write `feature_cache/flags/honeypot_flags.npy` and `feature_cache/flags/disqualifier_types.pkl`.

**Acceptance Criteria**:
- After `precompute.py` runs, `honeypot_flags.npy` exists with shape `[N]` uint8
- All candidates with `is_honeypot=True` in structured features have `honeypot_flags[i] == 1`
- Consulting-only candidates have `honeypot_flags[i] == 2`
- Clean candidates have `honeypot_flags[i] == 0`
- `disqualifier_types.pkl` maps all disqualified candidate IDs to their reason strings

**Complexity**: S  
**Dependencies**: Task 4.1, Task 2.3

---

## Phase 5: Ranking & Output

**Goal**: Implement final score assembly, top-100 selection, reasoning generation, and CSV writer.

---

### Task 5.1 — Implement Score Assembler (`src/ranking/assembler.py`)

**Description**: Load feature cache, compute JD embedding, compute cosine similarities in batches, apply all dimension scorers, assemble final weighted scores for all 100K candidates.

**Acceptance Criteria**:
- `assembler.run()` returns `DimScores` array for all candidates as numpy array `[N, 8]`
- Cosine similarity computation uses vectorized numpy (no Python loops over candidates)
- Disqualified candidates have `final_score = 0.0` (disqualifier_multiplier = 0.0)
- Score assembly for 100K candidates completes in < 60 seconds
- Pool-level normalization of semantic_similarity applied correctly (min-max)
- All dimension scores in [0.0, 1.0] (assert after computation)

**Complexity**: M  
**Dependencies**: Task 2.1, Task 3.1–3.6, Task 4.1

---

### Task 5.2 — Implement Top-100 Selector (`src/ranking/selector.py`)

**Description**: Select top-100 candidates from final scores, apply tie-breaking rules, assign ranks.

**Acceptance Criteria**:
- `selector.select(scores_array, candidate_ids, dim_scores)` returns sorted list of `(candidate_id, rank, score)` tuples
- Exactly 100 results with ranks 1–100, each exactly once
- Scores are monotonically non-increasing (assert)
- Tie-breaking: behavioral_signals → logistics_fit → experience_quality → candidate_id lexicographic
- No honeypot candidate appears in top 100 (assert with known honeypot test case)
- No consulting-only candidate appears in top 100 (assert)
- Unit test: given scores array with known top-5 → verify output ranks match expected

**Complexity**: S  
**Dependencies**: Task 5.1

---

### Task 5.3 — Implement Reasoning Generator (`src/ranking/reasoning.py`)

**Description**: Implement `ReasoningGenerator` that generates a unique, grounded reasoning string for each top-100 candidate using only facts from the candidate's actual data.

**Acceptance Criteria**:
- `generator.generate(candidate, scores, rank)` returns string ≤300 chars
- Generated string references the candidate's actual `current_title` and `years_of_experience`
- Generated string references at least one skill actually present in `candidate["skills"]`
- Generated string mentions the candidate's location or notice period
- For ranks 11–100: string mentions at least one concern/gap (e.g., "outside India", "high notice period", "no Python advanced")
- No string references a skill absent from the candidate's skills list (anti-hallucination test)
- 100 generated strings have ≥5 distinct structural patterns (variation check)
- Top-10 strings are measurably more positive in tone than ranks 90–100 strings (manual review checkpoint)

**Complexity**: M  
**Dependencies**: Task 5.2

---

### Task 5.4 — Implement CSV Writer with Validation (`src/output/writer.py`)

**Description**: Implement `SubmissionWriter` class that writes and validates the final CSV per submission spec.

**Acceptance Criteria**:
- `writer.write(ranked_candidates, output_path)` produces valid CSV
- Validates: exactly 100 rows, ranks 1–100 unique, scores monotonically non-increasing, all IDs in format CAND_XXXXXXX
- Raises `ValueError` with descriptive message if validation fails
- `writer.validate(path)` returns empty list for a valid submission.csv
- `writer.validate(path)` returns non-empty list for a CSV with 101 rows
- CSV encoding is UTF-8, rows end with `\n`
- Reasoning strings with commas are properly quoted (CSV escaping)

**Complexity**: S  
**Dependencies**: Task 5.2, Task 5.3

---

### Task 5.5 — Implement Ranking Orchestrator (`rank.py`)

**Description**: Wire together: load cache → compute similarities → assemble scores → select top 100 → generate reasoning → write CSV. Print timing for each phase.

**Acceptance Criteria**:
- `python rank.py --jd <path> --out submission.csv --cache-dir ./feature_cache` runs end-to-end
- Outputs a valid `submission.csv` that passes `SubmissionWriter.validate()`
- Total runtime < 120 seconds (Phase 2 target)
- Print total runtime to stdout
- `--audit-log` flag produces `ranking_audit.json` with correct stats
- Exit code 0 on success, appropriate codes on failure

**Complexity**: M  
**Dependencies**: Task 5.1, Task 5.2, Task 5.3, Task 5.4

---

## Phase 6: Validation & Testing

**Goal**: Ensure correctness, format compliance, and performance under constraints.

---

### Task 6.1 — Format Validation Suite

**Description**: Write a standalone validator script `scripts/validate_submission.py` that fully checks a submission.csv against the contest spec.

**Acceptance Criteria**:
- Checks: 100 rows, rank uniqueness, score monotonicity, candidate_id format, UTF-8 encoding
- Checks: no candidate_id appearing in the CSV that is NOT in candidates.jsonl (requires reading the dataset)
- Prints PASS or lists all failures
- Run against `sample_submission.csv` (provided sample) → should PASS format checks
- Script runtime < 30 seconds

**Complexity**: S  
**Dependencies**: Task 5.4

---

### Task 6.2 — Honeypot False Positive Audit

**Description**: Run the honeypot detector on `sample_candidates.json` (50 known candidates) and manually verify zero false positives.

**Acceptance Criteria**:
- None of the 50 sample candidates are falsely flagged as honeypots (review each flag manually)
- If any sample candidate is flagged, investigate and adjust thresholds in `ranking_config.yaml`
- Document the threshold decisions in a comment in `honeypot.py`

**Complexity**: S  
**Dependencies**: Task 4.1

---

### Task 6.3 — Hard Disqualifier Audit

**Description**: Verify that hard disqualifiers only apply to clearly unfit candidates by reviewing a sample of disqualified candidates from the full dataset.

**Acceptance Criteria**:
- Run precompute.py and review `disqualifier_types.pkl`
- Sample 20 consulting-only disqualified candidates → all should have 100% consulting firm career history
- Sample 20 non-technical disqualified candidates → all should have no AI/ML career history
- If false positives found, adjust consulting firm list or title list and re-run

**Complexity**: S  
**Dependencies**: Task 4.2, Task 2.3

---

### Task 6.4 — Sample Ranking Quality Check

**Description**: Run full pipeline on first 5,000 candidates (for speed) and inspect top-20 results for quality.

**Acceptance Criteria**:
- Top 5 candidates are ML Engineers, Data Scientists, or AI Engineers with relevant backgrounds
- No Marketing Managers, Accountants, or Graphic Designers appear in top 10 (unless they have genuine AI history in descriptions)
- At least 3 of top 10 show evidence of production deployment in their career descriptions
- The ranking is clearly better than the sample_submission.csv baseline
- Reasoning strings for top 10 are specific, factual, and relevant to the JD

**Complexity**: M  
**Dependencies**: Task 5.5

---

### Task 6.5 — Performance Benchmarking

**Description**: Profile the full pipeline end-to-end to confirm it meets the 5-minute constraint.

**Acceptance Criteria**:
- `time python run_pipeline.py --candidates <full dataset> --jd <jd> --out submission.csv` completes in ≤270 seconds (with 30s safety buffer)
- Peak RAM usage ≤ 8 GB (well within 16 GB limit) — measured with `psutil.Process.memory_info().rss`
- No network calls made during ranking — verified with `--network none` Docker flag or offline network isolation
- If runtime exceeds 270s: profile with `cProfile`, identify bottleneck, optimize (likely: increase batch_size, or switch to a smaller model)

**Complexity**: M  
**Dependencies**: Task 2.3, Task 5.5

---

### Task 6.6 — Unit Test Coverage

**Description**: Write unit tests for all scoring functions. Target 80%+ line coverage on `src/scoring/`.

**Acceptance Criteria**:
- `pytest tests/` runs without errors
- `pytest --cov=src tests/` shows ≥80% coverage for `src/scoring/`
- All tests pass in < 60 seconds (no full dataset reads in unit tests)
- Tests use synthetic candidate dicts (don't depend on actual dataset files)

**Complexity**: M  
**Dependencies**: All Phase 3, 4 tasks

---

## Phase 7: Sandbox Deployment (Optional)

**Goal**: Build a minimal interactive demo for the defend-your-work interview.

---

### Task 7.1 — Dockerize the Pipeline

**Description**: Create a `Dockerfile` that builds a self-contained image with all dependencies, model weights baked in, and a CMD that runs `run_pipeline.py`.

**Acceptance Criteria**:
- `docker build -t ai-recruiter-copilot .` completes successfully
- `docker run --network none -v $(pwd)/data:/data ai-recruiter-copilot` runs the full pipeline with no network access
- Total Docker image size < 3 GB
- The container produces a valid `submission.csv` when given `candidates.jsonl` and `job_description.json`
- Model weights are in the image (not downloaded at runtime)

**Complexity**: M  
**Dependencies**: Task 6.5

---

### Task 7.2 — Streamlit Demo (Optional / Stretch Goal)

**Description**: Build a minimal `streamlit_app.py` that lets a user paste a candidate JSON and see their computed scores.

**Acceptance Criteria**:
- App loads pre-computed feature cache on startup
- User can paste or select a candidate_id and see all 6 dimension scores
- User can see the generated reasoning string
- App runs locally with `streamlit run streamlit_app.py`
- Does not require re-running precompute.py (uses cached features)

**Complexity**: M  
**Dependencies**: Task 5.1, Task 5.3

---

## Task Dependency Graph

```
Phase 0: 0.1 → 0.2 → 0.3 → 0.4
                 ↓         ↓
Phase 1:        1.1 → 1.2 → 1.3 → 1.4
                       ↓
Phase 2:              2.1     2.2 → 2.3
                       ↓       ↓
Phase 3:          3.1(2.1+2.2) 3.2–3.6(2.2)
                       ↓
Phase 4:              4.1 → 4.2(+2.3)
                       ↓
Phase 5:    5.1(3.x+4.1) → 5.2 → 5.3 → 5.4 → 5.5
                                              ↓
Phase 6:                          6.1 6.2 6.3 6.4 6.5 6.6
                                                      ↓
Phase 7:                                          7.1 → 7.2
```

---

## Effort Summary

| Phase | Tasks | Complexity | Est. Days |
|---|---|---|---|
| Phase 0: Setup | 4 tasks | 4×S | 0.5 days |
| Phase 1: Data Pipeline | 4 tasks | 3×S + 1×M | 1.5 days |
| Phase 2: Semantic Layer | 3 tasks | 1×S + 1×M + 1×L | 2.0 days |
| Phase 3: Structured Scoring | 6 tasks | 3×S + 2×M + 1×L | 3.0 days |
| Phase 4: Honeypot Detection | 2 tasks | 1×S + 1×M | 1.0 day |
| Phase 5: Ranking & Output | 5 tasks | 2×S + 3×M | 2.0 days |
| Phase 6: Validation | 6 tasks | 3×S + 3×M | 2.0 days |
| Phase 7: Deployment | 2 tasks | 2×M | 1.5 days |
| **Total** | **32 tasks** | | **~13.5 days** |

*Note: S=Small (~2–4h), M=Medium (~4–8h), L=Large (~8–16h). These are solo-developer estimates for a developer experienced with Python ML engineering.*
