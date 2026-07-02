# Implementation Plan

## Overview

Build the AI Recruiter Copilot: a CPU-only, offline candidate ranking system for the India Run AI & Data Challenge. The system reads 100K candidates from JSONL, computes semantic embeddings and structured features, detects honeypots and hard disqualifiers, scores across 6 dimensions, and outputs a top-100 submission CSV. Implemented in two phases: precompute.py (offline feature extraction) and rank.py (fast online ranking).

## Tasks

- [x] 1. Backend scaffold and configuration files
  - Create `backend/` directory with FastAPI app scaffold
  - Create `backend/app/main.py` with FastAPI app, CORS, health check endpoint
  - Create `backend/app/core/config.py` with Settings (pydantic-settings), reading from `.env`
  - Create `backend/app/core/database.py` with SQLAlchemy async engine, session factory
  - Create `backend/app/models/__init__.py`
  - Create `backend/app/models/candidate.py` with Candidate, CareerHistory, Education, Skill, Certification, Language SQLAlchemy models
  - Create `backend/app/models/redrob_signals.py` with RedrobSignal SQLAlchemy model (1-to-1 with Candidate)
  - Create `backend/app/models/job_description.py` with JobDescription SQLAlchemy model
  - Create `backend/app/models/ranking.py` with RankingRun and CandidateRank SQLAlchemy models
  - Create `backend/app/schemas/` directory with Pydantic v2 schemas matching all models
  - Create `backend/app/api/v1/router.py` with APIRouter registering all route modules
  - Create `backend/app/api/v1/endpoints/candidates.py` with GET /candidates and GET /candidates/{id} stubs
  - Create `backend/app/api/v1/endpoints/ranking.py` with POST /ranking/run and GET /ranking/{run_id} stubs
  - Create `backend/app/api/v1/endpoints/health.py` with GET /health
  - Create `backend/requirements.txt` with pinned: fastapi==0.115.0, uvicorn==0.30.6, sqlalchemy==2.0.36, asyncpg==0.30.0, alembic==1.14.0, pydantic-settings==2.6.1, python-dotenv==1.0.1
  - Create `backend/.env.example` with DATABASE_URL, SECRET_KEY, CORS_ORIGINS placeholders
  - Create `backend/alembic.ini` and `backend/alembic/env.py` wired to SQLAlchemy models
  - Create `config/ranking_config.yaml` with full config (weights, JD params, scoring tiers, honeypot thresholds) per docs/API_Spec.md §4
  - Create `config/jd_text.py` with JD_TEXT constant (enriched JD text for embedding)
  - Create `config/reasoning_templates.yaml` with at least 3 templates per tier (top-10, mid-11-50, lower-51-100)
  - Create root `.gitignore` covering: feature_cache/, __pycache__/, *.pyc, .env, node_modules/, .next/, *.npy, *.pkl
  - Create `docker-compose.yml` with services: postgres (port 5432), backend (port 8000), frontend (port 3000)
  - Create root `README.md` with setup instructions, env setup, and how to run each service

- [x] 2. Frontend scaffold: Next.js 15 + TypeScript + Tailwind + shadcn/ui
  - Run `npx create-next-app@latest frontend --typescript --tailwind --app --src-dir --import-alias "@/*"` in project root
  - Install shadcn/ui with `npx shadcn@latest init` (New York style, slate base color, CSS variables)
  - Add shadcn components: button, card, badge, table, input, select, skeleton, separator, progress, toast
  - Create `frontend/src/lib/api.ts` with typed API client (fetch wrapper pointing to backend)
  - Create `frontend/src/types/candidate.ts` with TypeScript interfaces matching candidate schema
  - Create `frontend/src/types/ranking.ts` with RankingRun and CandidateRank interfaces
  - Create `frontend/src/app/layout.tsx` with root layout (font, Toaster)
  - Create `frontend/src/app/page.tsx` as landing page with project title and nav links
  - Create `frontend/src/app/candidates/page.tsx` as stub candidates list page
  - Create `frontend/src/app/ranking/page.tsx` as stub ranking page
  - Create `frontend/src/components/layout/Navbar.tsx` with links to Candidates and Ranking pages

- [x] 3. Streaming JSONL reader and schema validator
  - Create `src/__init__.py`
  - Create `src/data/__init__.py`
  - Create `src/data/reader.py` with CandidateStreamReader class: streaming line-by-line, yields dicts, implements get_stats() returning total_lines/valid/skipped/errors
  - Create `src/data/validator.py` with validate(candidate) → (bool, list[str]) checking all required fields per candidate_schema.json
  - Write `tests/test_reader.py` covering: empty file, valid line, invalid JSON line, missing required field
  - Run `pytest tests/test_reader.py` and confirm all tests pass

- [x] 4. Candidate normalization: structured feature extraction
  - Create `src/features/__init__.py`
  - Create `src/features/structured.py` with StructuredFeatureExtractor.extract(candidate) → dict covering all ~50 fields from docs/DatabaseSchema.md §3.1
  - Implement experience features: years_exp, derived_years_exp, career_history_count, tenure metrics
  - Implement company type features: product_company_months, consulting_company_months, product_company_ratio, consulting_only flag
  - Implement title/seniority features: infer_seniority, title_seniority_scores, seniority_trend
  - Implement skills features: ai_ml_skill_count, core_jd_skill_count, skill_depth_score, has_embedding_retrieval, has_vector_db, has_python_advanced, has_evaluation_framework
  - Implement production evidence features: production_evidence_score, has_ab_testing, has_latency_sla, has_real_users
  - Implement behavioral features: open_to_work, days_since_active, notice_period_days, recruiter_response_rate
  - Implement location/logistics features: location_fit_score, salary_alignment_score
  - Write `tests/test_structured.py` with cases: AI engineer candidate, non-technical candidate, consulting-only candidate
  - Run `pytest tests/test_structured.py` and confirm all tests pass

- [x] 5. JD parser and candidate profile text builder
  - Create `src/data/jd_parser.py` with parse_jd_docx(path) → dict extracting: title, company, location, experience_years, must_have_skills, nice_to_have_skills, hard_disqualifiers, preferred_locations, salary_range_lpa, full_text
  - Use python-docx to extract text from .docx file
  - Create `scripts/parse_jd.py` CLI: `python scripts/parse_jd.py --jd ./India_runs_data_and_ai_challenge/job_description.docx`
  - Create `config/job_description.json` as the parsed structured JD output (cached)
  - Create `src/features/text_builder.py` with build_candidate_text(candidate) → str (≤4096 chars, headline + summary + career descriptions + skill names, most recent first, current role prefixed with "Currently: ")
  - Implement build_jd_text() → str that returns JD_TEXT from config/jd_text.py
  - Ensure truncation to 4096 chars with priority on recent roles
  - Write `tests/test_jd_parser.py` confirming all required JD fields are extracted
  - Write `tests/test_text_builder.py` with cases: full candidate, empty summary, no skills, candidate with 10 career history entries
  - Run `pytest tests/test_jd_parser.py tests/test_text_builder.py` and confirm all pass

- [x] 6. Honeypot detector and hard disqualifier checker
  - Create `src/scoring/__init__.py`
  - Create `src/scoring/honeypot.py` with HoneypotDetector class implementing: check_tenure_impossible, check_expert_zero_duration, check_skills_ratio, check_title_desc_mismatch
  - Implement HardDisqualifierChecker class with: is_consulting_only, is_non_technical_no_ai
  - HoneypotDetector.check() returns (is_honeypot: bool, triggered_flags: list[str]) per docs/API_Spec.md §3.4
  - HardDisqualifierChecker.check() returns (is_disqualified: bool, reason: str) per docs/API_Spec.md §3.4
  - Write `tests/test_honeypot.py` covering: all 4 honeypot flags, consulting-only, non-technical, false positive (Marketing Manager who pivoted to ML)
  - Run `pytest tests/test_honeypot.py` and confirm all tests pass

- [x] 7. Embedding encoder and feature cache
  - Create `src/features/embedding.py` with EmbeddingEncoder class wrapping sentence-transformers
  - Implement load_model() using SentenceTransformer('all-MiniLM-L6-v2') with device='cpu'
  - Implement encode_batch(texts, normalize=True) → np.ndarray shape [len(texts), 384] dtype float32
  - Implement encode_single(text) → np.ndarray[384]
  - Add embedding_dim property returning 384
  - Create `src/features/cache.py` with FeatureCache class implementing: save_embedding_batch/load_embedding_batch (.npy), save_structured_batch/load_structured_batch (.pkl), save_meta/load_meta (meta.json), save_jd_embedding/load_jd_embedding
  - Write `tests/test_embedding.py` confirming: similar sentences cosine sim > 0.9, dissimilar < 0.5, output shape correct, norms ≈ 1.0
  - Write `tests/test_cache.py` with round-trip tests for embeddings and structured features
  - Run `pytest tests/test_embedding.py tests/test_cache.py` and confirm all pass

- [x] 8. Precompute pipeline: orchestrate Phase 1 end-to-end
  - Create `precompute.py` CLI with args: --candidates, --cache-dir (default ./feature_cache), --batch-size (default 512), --model, --config, --workers, --verbose
  - Wire pipeline: StreamReader → Validator → HoneypotDetector + HardDisqualifierChecker → TextBuilder → batch accumulate 512 → EmbeddingEncoder.encode_batch → FeatureCache.save_batch + StructuredFeatureExtractor.extract
  - Write honeypot_flags.npy and disqualifier_types.pkl to feature_cache/flags/
  - Write meta.json with id_to_index mapping and run statistics
  - Implement exit codes: 0 success, 1 file not found, 2 disk space, 3 model load fail, 4 >5% invalid
  - Print progress every 5000 candidates when --verbose
  - Test on first 1000 candidates: `python precompute.py --candidates <path> --limit 1000`
  - Verify feature_cache/ directory structure is created correctly

- [x] 9. Dimension scorers: all 6 scoring dimensions
  - Create `src/scoring/dim_semantic.py` implementing score_semantic_skill_fit per requirements §4
  - Create `src/scoring/dim_experience.py` implementing score_experience_quality per requirements §5
  - Create `src/scoring/dim_progression.py` implementing score_career_progression per requirements §6
  - Create `src/scoring/dim_behavioral.py` implementing score_behavioral_signals per requirements §7
  - Create `src/scoring/dim_logistics.py` implementing score_logistics_fit per requirements §8
  - Create `src/scoring/dim_integrity.py` implementing score_profile_integrity per requirements §9
  - Each scorer returns a float in [0.0, 1.0] normalized across candidate pool
  - Implement DimScores dataclass and final_score() combining all 6 dimensions with weights from config per requirements §10
  - Write `tests/test_dimensions.py` with at least one test case per scorer dimension
  - Run `pytest tests/test_dimensions.py` and confirm all pass

- [x] 10. Ranking assembler, selector, reasoning generator, and output writer
  - Create `src/ranking/assembler.py` loading all cached features, computing cosine similarity in batches, applying all 6 dimension scorers, assembling final weighted scores per docs/RankingLogic.md §1
  - Create `src/ranking/selector.py` with select() returning top-100 with tie-breaking on behavioral_signals per requirements §10
  - Create `src/ranking/reasoning.py` with ReasoningGenerator: generate() using template filling from actual candidate data (no hallucination), extract_facts() extracting key facts, varies templates by rank tier (top-10 / 11-50 / 51-100), returns string ≤300 chars
  - Create `src/output/__init__.py`
  - Create `src/output/writer.py` with SubmissionWriter.write() and validate() per docs/API_Spec.md §3.7
  - Create `rank.py` CLI with args: --jd, --out, --cache-dir, --config, --top-n, --audit-log, --verbose; exit codes per docs/API_Spec.md §1.2
  - Create `run_pipeline.py` wrapping both precompute + rank in sequence
  - Write `tests/test_output_validation.py` testing CSV format, 100 rows, ranks 1-100 unique, monotonic scores
  - Run `pytest tests/test_output_validation.py` and confirm all pass
  - Run on 1000-candidate sample, verify submission.csv format passes validation

## Task Dependency Graph

```json
{
  "waves": [
    {"wave": 1, "tasks": ["1", "2", "3"]},
    {"wave": 2, "tasks": ["4"]},
    {"wave": 3, "tasks": ["5", "6"]},
    {"wave": 4, "tasks": ["7"]},
    {"wave": 5, "tasks": ["8"]},
    {"wave": 6, "tasks": ["9"]},
    {"wave": 7, "tasks": ["10"]}
  ]
}
```

## Notes

- All implementation must use CPU-only libraries. No CUDA, GPU, or external API calls during ranking.
- Sentence transformer model: `sentence-transformers/all-MiniLM-L6-v2` (384-dim, ~23MB)
- Target runtime: Phase 1 ≤180s + Phase 2 ≤90s = ≤270s total on 4-core CPU with 16 GB RAM
- Feature cache target size: ~360 MB (154 MB embeddings + 200 MB structured features + flags)
- Peak memory budget: ~1.5 GB well within 16 GB limit
- Reasoning strings must be fact-grounded from actual candidate data only — no hallucination
- Hard disqualifier and honeypot checks run before expensive embedding scoring to avoid wasted computation
- Dataset reference date for recency calculations: 2025-06-01
