# DependencyAnalysis.md — Repository Dependency Audit

**Date**: 2026-07-05  
**Scope**: Five files audited for production reachability from `app.main`

---

## Results Summary

| File | Classification | Reachable from app.main | Used by API endpoint | Used by tests |
|------|---------------|------------------------|---------------------|---------------|
| `src/features/embedding.py` | **USED IN PRODUCTION** | ✅ YES | ✅ YES (indexing + analysis) | ✅ YES |
| `src/ranking/engine.py` | **USED IN PRODUCTION** | ✅ YES | ✅ YES (analysis endpoint) | ✅ YES |
| `precompute.py` | **CLI ONLY** | ❌ NO | ❌ NO | ❌ NO |
| `rank.py` | **CLI ONLY** | ❌ NO | ❌ NO | ❌ NO |
| `config/ranking_config.yaml` | **CLI ONLY** | ❌ NO (path stored, file never opened) | ❌ NO | ❌ NO |

---

## Complete Import Graph: app.main → Production Files

```
app.main (main.py)
│
├── app.api.v1.router (router.py)
│   └── app.api.v1.endpoints.platform (platform.py)   ← THE PRODUCTION HUB
│       │
│       ├── src.features.embedding                     [PRODUCTION]
│       │   └── _MODEL_CACHE         (dict — shared with model_service)
│       │   └── EmbeddingEncoder     (instantiated in _get_encoder())
│       │
│       ├── src.features.structured                    [PRODUCTION]
│       │   └── _classify_specialization_with_confidence
│       │   └── classify_candidate_role_category
│       │   └── HARD_DISQUALIFIER_TITLES
│       │   └── StructuredFeatureExtractor
│       │
│       ├── src.features.text_builder                  [PRODUCTION]
│       │   └── build_candidate_text
│       │
│       ├── src.scoring.quality                        [PRODUCTION]
│       │   └── calculate_candidate_quality_score
│       │
│       └── src.ranking.engine                         [PRODUCTION]
│           └── COMPATIBLE_CATEGORIES    (used in analysis pre-flight + category filtering)
│           └── UnifiedRankingEngine     (used in run_analysis endpoint)
│           └── validate_tuple           (used to validate LLM response)
│
└── app.services.model_service (model_service.py)      [PRODUCTION]
    └── src.features.embedding
        └── _MODEL_CACHE             (read/write for cache-hit detection)
        └── (EmbeddingEncoder NOT instantiated here — model_service uses SentenceTransformer directly)
```

---

## 1. `src/features/embedding.py`

**Classification: USED IN PRODUCTION**

### How it reaches app.main

```
app.main
  → app.api.v1.endpoints.platform  (platform.py, line 388)
      → from src.features.embedding import EmbeddingEncoder
        Called inside: _get_encoder()
        Triggered by:  POST /upload (indexing) and POST /analyze (analysis)

app.main
  → app.services.model_service  (model_service.py, lines 146, 250, 441)
      → from src.features.embedding import _MODEL_CACHE
        Called inside: _do_load() — cache check/write
        Called inside: reset()    — cache clear
```

### Evidence

- `platform.py` line 388: `from src.features.embedding import EmbeddingEncoder` — inside `_get_encoder()`, called by both the indexing background task and the analysis endpoint
- `model_service.py` lines 146, 250, 441: `from src.features.embedding import _MODEL_CACHE` — used as a shared dict to check/populate the singleton cache

### In Docker?

Yes. `COPY src/ /app/src/` in Dockerfile copies all of `src/` into the container. `PYTHONPATH=/app` ensures imports resolve.

### Also used by tests?

Yes — `tests/test_embedding.py` directly imports and tests `EmbeddingEncoder`.

---

## 2. `src/ranking/engine.py`

**Classification: USED IN PRODUCTION**

### How it reaches app.main

```
app.main
  → app.api.v1.endpoints.platform  (platform.py)
      → from src.ranking.engine import COMPATIBLE_CATEGORIES   (line 2651)
        Used in: run_analysis() pre-flight check (which role-index files to verify)
        Used in: candidate category filtering loop

      → from src.ranking.engine import UnifiedRankingEngine    (line 3301)
        Used in: run_analysis() — wraps LLM-scored ranking results

      → from src.ranking.engine import validate_tuple          (line 3310)
        Used in: run_analysis() — validates LLM engine response tuple
```

### Note on which parts of engine.py are actually exercised

`platform.py` does **not** call `engine.rank_candidates()` for the main FAISS search. It uses its own FAISS + scoring pipeline inline. `UnifiedRankingEngine` is only called for LLM re-ranking post-FAISS. The `COMPATIBLE_CATEGORIES` dict is used for category filtering. `validate_tuple` is a utility.

The dimension auto-correction block (CRITICAL-4 from the embedding audit) is inside `rank_candidates()` — it IS reachable from production via the LLM re-ranking path.

### In Docker?

Yes. `COPY src/ /app/src/` and `PYTHONPATH=/app`.

### Also used by tests?

Yes — `tests/test_candidate_metadata_mapping.py` imports `UnifiedRankingEngine`, `validate_ranking_payload`, `resolve_candidate_metadata` from `src.ranking.engine`.

---

## 3. `precompute.py`

**Classification: CLI ONLY**

### Import graph from app.main

```
app.main → [NO PATH]
```

`precompute.py` is **never imported** by any file under `backend/`. A full grep across `backend/**/*.py` finds zero references to `precompute`.

The only import is in `run_pipeline.py`:
```python
from precompute import run_precompute, _build_parser   # run_pipeline.py line 30
```

`run_pipeline.py` is itself a standalone CLI entry point with no connection to FastAPI.

### In Docker?

`precompute.py` lives at the project root. The Dockerfile does `COPY backend/ /app/` and `COPY src/ /app/src/` — it does **not** copy `precompute.py` into the container image. The file is not present on Render.

### Is it dead code?

No — it is an offline CLI tool for the competition/challenge pipeline. It is live code, just not production backend code.

### Used by tests?

No — no test file imports it directly.

---

## 4. `rank.py`

**Classification: CLI ONLY**

### Import graph from app.main

```
app.main → [NO PATH]
```

`rank.py` is **never imported** by any file under `backend/`. A full grep across `backend/**/*.py` finds zero references to `rank`.

The only import is in `run_pipeline.py`:
```python
from rank import run_rank, _build_parser   # run_pipeline.py line 31
```

### In Docker?

`rank.py` lives at the project root. The Dockerfile does not copy the project root `.py` files — only `backend/` and `src/`. `rank.py` is **not present on Render**.

### Dependencies of rank.py

```
rank.py
  → src.features.cache          (FeatureCache — reads precomputed .npy files from disk)
  → src.ranking.assembler       (ScoreAssembler)
  → src.ranking.selector        (select_top_n)
  → src.ranking.reasoning       (ReasoningGenerator)
  → src.output.writer           (SubmissionWriter)
```

Note: `rank.py` uses `src.ranking.assembler` / `src.ranking.selector` — these are **different modules** from `src.ranking.engine`. `rank.py` never imports `src.ranking.engine`.

### Is it dead code?

No — it is an offline CLI tool for the challenge pipeline.

### Used by tests?

No — no test file imports it directly.

---

## 5. `config/ranking_config.yaml`

**Classification: CLI ONLY**

### Is it read by the production backend?

**No.** Here is the complete evidence:

`backend/app/core/config.py` stores `ranking_config_path: str = "./config/ranking_config.yaml"` as a field on the `Settings` object. This makes `settings.ranking_config_path` available at runtime.

A full grep across `backend/**/*.py` finds **zero calls** to `settings.ranking_config_path`. No backend code ever reads, opens, or parses the YAML file. The field exists purely as a relic of an earlier design where the backend was going to use this config.

### Who actually reads it?

```
rank.py:          _load_config(args.config)   # --config ./config/ranking_config.yaml (CLI arg)
precompute.py:    --config arg (described as "currently informational")
run_pipeline.py:  passes --config to both sub-scripts
```

### In Docker?

The Dockerfile does `COPY config/ /app/config/` — so the YAML file **is present on Render**, but since no backend code opens it, it has no effect.

### Is it dead code?

From the production backend's perspective: **yes, effectively dead**. The file is present in the container but never read. For the CLI pipeline: it is alive and contains active configuration (model name, weights, etc.).

---

## Implications for the Embedding Migration

| File | Migration risk | Why |
|------|---------------|-----|
| `src/features/embedding.py` | 🔴 HIGH | USED IN PRODUCTION — wrong default model (`bge-base`) will cause dim mismatch at runtime |
| `src/ranking/engine.py` | 🔴 HIGH | USED IN PRODUCTION — dimension auto-correction block can reload `bge-large` → OOM kill |
| `precompute.py` | 🟡 MEDIUM | CLI ONLY — but if run offline it produces 1024-dim FAISS indexes incompatible with production |
| `rank.py` | 🟢 LOW | CLI ONLY, never touches FAISS directly |
| `config/ranking_config.yaml` | 🟡 MEDIUM | CLI ONLY for backend, but `precompute.py` reads the model name from it — still specifies `bge-large` |

The two files that **must** be fixed before the migration can be considered complete are `src/features/embedding.py` (default model) and `src/ranking/engine.py` (dimension auto-correction block), because both are reachable from `app.main` via `platform.py`.
