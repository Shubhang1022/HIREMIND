# EmbeddingMigrationAudit.md

**Audit Date**: 2026-07-05  
**Migration**: `BAAI/bge-large-en-v1.5` (1024-dim) → `BAAI/bge-small-en-v1.5` (384-dim)  
**Method**: Full repository grep + static analysis (no files modified)

---

## Summary

| Category | Count |
|----------|-------|
| **CRITICAL** | 6 |
| **WARNING** | 12 |
| **INFO** | 18 |
| **Total matches** | 36 |

**Migration completeness**: ⚠️ INCOMPLETE  
**Confidence score**: **42 / 100** — significant critical issues remain

---

## ⛔ CRITICAL ISSUES (will break indexing, FAISS, or retrieval)

---

### CRITICAL-1 — `src/features/embedding.py` default model is `bge-base`, not `bge-small`

| Field | Value |
|-------|-------|
| **File** | `src/features/embedding.py` line 20 |
| **Current code** | `def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5") -> None:` |
| **Problem** | `EmbeddingEncoder`'s default is still `bge-base` (768-dim). The Dockerfile and config.py both use `bge-small` (384-dim). When `EmbeddingEncoder` is instantiated without an explicit model name (e.g., in CLI scripts, tests, or recovery paths), it defaults to `bge-base`, producing 768-dim vectors. These will be **incompatible** with any previously indexed FAISS files built with `bge-small` (384-dim) vectors — silent shape mismatch during search. |
| **Must change?** | YES |
| **Suggested fix** | `def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:` |
| **Severity** | Breaks analysis: FAISS search will fail or return garbage if the encoder dim doesn't match the index dim |

---

### CRITICAL-2 — `config/ranking_config.yaml` still specifies `bge-large` as primary model

| Field | Value |
|-------|-------|
| **File** | `config/ranking_config.yaml` line 7 |
| **Current code** | `embedding_model: "BAAI/bge-large-en-v1.5"` |
| **Problem** | The YAML config is the **authoritative configuration** used by the standalone CLI pipeline (`precompute.py`, `rank.py`). The `ranking_config.yaml` still specifies `bge-large` (1024-dim) as the primary model. Running the offline pipeline produces 1024-dim embeddings; the production backend uses `bge-small` (384-dim). A FAISS index built offline with 1024-dim vectors cannot be searched with 384-dim query vectors. |
| **Must change?** | YES |
| **Suggested fix** | `embedding_model: "BAAI/bge-small-en-v1.5"` and remove or update `fallback_model` |

---

### CRITICAL-3 — `precompute.py` hardcodes `EMBEDDING_DIM_DEFAULT = 1024`

| Field | Value |
|-------|-------|
| **File** | `precompute.py` line 52 |
| **Current code** | `EMBEDDING_DIM_DEFAULT = 1024` |
| **Problem** | This constant is used to pre-allocate numpy arrays in `_flush_batch()`. With `bge-small` (384-dim), the actual encoded dimension is 384 but the array is pre-allocated at 1024. The code does a runtime resize check (`if actual_dim != embedding_dim`) which partially mitigates this, but (a) the resize produces log warnings, (b) there may be callers that pass `embedding_dim` as a parameter and use 1024 without going through `_flush_batch`, (c) the metadata written to disk at line 331 (`"embedding_dim": embedding_dim`) will log `1024` even though embeddings are 384-dim. |
| **Must change?** | YES |
| **Suggested fix** | `EMBEDDING_DIM_DEFAULT = 384` — or better, derive it lazily from `encoder.embedding_dim` after first encode |

---

### CRITICAL-4 — `src/ranking/engine.py` hardcoded dimension alignment: `elif dim == 1024: model = bge-large`

| Field | Value |
|-------|-------|
| **File** | `src/ranking/engine.py` lines 413–420 |
| **Current code** | `if dim == 384: self.encoder.model_name = "BAAI/bge-small-en-v1.5"` / `elif dim == 1024: self.encoder.model_name = "BAAI/bge-large-en-v1.5"` |
| **Problem** | This is a "dimension auto-correction" block that looks at candidate embedding dimension and mutates the encoder's model name. If any FAISS index from the old pipeline (1024-dim) is retrieved from storage, this code silently switches the encoder back to `bge-large`, causing a 1.34 GB download on Render → OOM kill. It is also incomplete: `dim == 768` (bge-base) is not handled. |
| **Must change?** | YES — entire block should be removed or replaced with an assertion |
| **Suggested fix** | Remove the auto-correction block. Instead, raise a clear error if `self.encoder.embedding_dim != dim` with message "FAISS index dimension mismatch — re-index candidates" |

---

### CRITICAL-5 — `tests/test_embedding.py` asserts dim == 1024 for `EmbeddingEncoder()`

| Field | Value |
|-------|-------|
| **File** | `tests/test_embedding.py` lines 29–35, 66–67 |
| **Current code** | `assert result.shape == (2, 1024)` / `assert encoder.embedding_dim == 1024` |
| **Problem** | `EmbeddingEncoder()` with no args currently defaults to `bge-base` (768-dim). Both assertions will fail. The docstring says "1024 for BAAI/bge-large-en-v1.5". The fixture runs `EmbeddingEncoder()` with no model name — so it loads whatever the default is. With the current `bge-base` default the tests fail with shape `(2, 768)`. After CRITICAL-1 is fixed to `bge-small`, they'll fail with shape `(2, 384)`. The tests have **never been updated** through any migration step. |
| **Must change?** | YES — will fail CI on every run |
| **Suggested fix** | Update all shape assertions to `384`, update docstrings, or explicitly construct `EmbeddingEncoder(model_name="BAAI/bge-small-en-v1.5")` |

---

### CRITICAL-6 — `tests/test_candidate_metadata_mapping.py` MockEncoder hardcodes `dim = 1024`

| Field | Value |
|-------|-------|
| **File** | `tests/test_candidate_metadata_mapping.py` lines 25–33 |
| **Current code** | `def __init__(self, dim: int = 1024) -> None:` |
| **Problem** | `MockEncoder` returns 1024-dim vectors by default. `UnifiedRankingEngine` receives this mock and passes it to the FAISS block in `engine.py`. If `engine.py`'s dimension alignment code (CRITICAL-4) is still present, it will try to load `bge-large`. The mock will be replaced by a real encoder silently. Even if that code is removed: any test that builds a FAISS index with 1024-dim mock vectors and then tries to search with 384-dim real embeddings will get a shape mismatch error. |
| **Must change?** | YES |
| **Suggested fix** | `def __init__(self, dim: int = 384) -> None:` |

---

## ⚠️ WARNING ISSUES

---

### WARNING-1 — `src/ranking/engine.py` uses `IndexHNSWFlat` (not `IndexFlatIP`)

| Field | Value |
|-------|-------|
| **File** | `src/ranking/engine.py` line 438 |
| **Current code** | `index = faiss.IndexHNSWFlat(dim, 32, faiss.METRIC_INNER_PRODUCT)` |
| **Problem** | The production indexing pipeline (`platform.py`) builds `IndexFlatIP`. The analysis engine (`engine.py`) builds `IndexHNSWFlat` for in-memory re-ranking. These two code paths use **different FAISS index types** for the same data. `IndexHNSWFlat` is approximate; `IndexFlatIP` is exact. Results will differ. This is not a dimension issue but is a consistency issue introduced by the codebase having two independent FAISS implementations. |
| **Must change?** | WARNING — not a crash but unexpected inconsistency |
| **Suggested fix** | Standardize on one index type. `IndexFlatIP` is exact and correct for small-to-medium datasets |

---

### WARNING-2 — `DeploymentChecklist.md` example shows `bge-base-en-v1.5` as the recommended EMBEDDING_MODEL_NAME

| Field | Value |
|-------|-------|
| **File** | `DeploymentChecklist.md` lines 14, 88–92 |
| **Current code** | `EMBEDDING_MODEL_NAME: BAAI/bge-base-en-v1.5` in table; log examples show `bge-base` |
| **Problem** | Will mislead operators into setting `EMBEDDING_MODEL_NAME=BAAI/bge-base-en-v1.5` which triggers a 438 MB runtime download on Render free tier → OOM kill |
| **Must change?** | YES — documentation error with production impact |
| **Suggested fix** | Update to `BAAI/bge-small-en-v1.5` throughout |

---

### WARNING-3 — `EmbeddingExecutionTimeline.md` example shows `bge-large` and `dim=1024`

| Field | Value |
|-------|-------|
| **File** | `EmbeddingExecutionTimeline.md` lines 3, 22, 24, 47 |
| **Current code** | References `BAAI/bge-large-en-v1.5`, `ram=1024.8MB`, `dim=1024` |
| **Problem** | Misleading documentation — operators expecting these log values will think something is wrong when they see `dim=384` and `ram=270MB` |
| **Must change?** | Recommended |

---

### WARNING-4 — `FinalProductionValidation.md` example log shows `bge-base` and `dim=768`

| Field | Value |
|-------|-------|
| **File** | `FinalProductionValidation.md` lines 105, 110–111 |
| **Current code** | `model=BAAI/bge-base-en-v1.5`, `embedding_dim=768` |
| **Problem** | Incorrect expected log values — will confuse operators validating a fresh deployment |
| **Must change?** | Recommended |

---

### WARNING-5 — `BackgroundWorkerAudit.md` and `EmbeddingAudit.md` still reference `bge-large`

| Field | Value |
|-------|-------|
| **Files** | `BackgroundWorkerAudit.md` line 86, `EmbeddingAudit.md` line 7, `FlowAudit.md` line 89 |
| **Problem** | Stale documentation. Operators reading these docs will be confused about which model is active |

---

### WARNING-6 — `.kiro/specs/india-run-ai-copilot/tasks.md` specifies `all-MiniLM-L6-v2` (384-dim)

| Field | Value |
|-------|-------|
| **File** | `.kiro/specs/india-run-ai-copilot/tasks.md` line 153 |
| **Current code** | `sentence-transformers/all-MiniLM-L6-v2` (384-dim, ~23MB) |
| **Problem** | Spec still specifies the original challenge model. This is now a third model not used anywhere in the production pipeline. The spec dimension (384) happens to match `bge-small`, but the model name is wrong |

---

### WARNING-7 — `docs/Architecture.md` and `docs/API_Spec.md` reference `all-MiniLM-L6-v2`

| Field | Value |
|-------|-------|
| **Files** | `docs/Architecture.md` lines 155–156, 296; `docs/API_Spec.md` lines 19, 32, 46, 193, 380 |
| **Problem** | Architecture docs reference a completely different model than what is deployed. `docs/API_Spec.md` still says `sentence-transformers/all-MiniLM-L6-v2` as the default |

---

### WARNING-8 — `docs/DatabaseSchema.md` records `embedding_dim: 384` with model `all-MiniLM-L6-v2`

| Field | Value |
|-------|-------|
| **File** | `docs/DatabaseSchema.md` lines 219–221 |
| **Problem** | Dimension is accidentally correct (384) but model name is wrong |

---

### WARNING-9 — `ArchitectureChanges.md` documents the migration to `bge-base`, not `bge-small`

| Field | Value |
|-------|-------|
| **File** | `ArchitectureChanges.md` lines 58–61, 105 |
| **Problem** | This document records a previous partial migration to `bge-base`. Now that the migration has moved further to `bge-small`, this document is stale and will confuse anyone reading the migration history |

---

### WARNING-10 — `docs/DatasetAnalysis.md` mentions ~1 GB for BGE-base

| Field | Value |
|-------|-------|
| **File** | `docs/DatasetAnalysis.md` line 20 |
| **Problem** | Old sizing note referencing BGE-base memory footprint |

---

### WARNING-11 — `rank.py` uses `settings.embedding_model` — indirect risk

| Field | Value |
|-------|-------|
| **File** | `rank.py` line 178 |
| **Current code** | `encoder = EmbeddingEncoder(model_name=settings.embedding_model)` |
| **Problem** | This correctly reads `settings.embedding_model` which is now `bge-small`. However, if `config/ranking_config.yaml` is loaded and overrides `settings.embedding_model` at runtime, it would switch back to `bge-large`. Need to verify the YAML is not being used to override the config object at runtime |

---

### WARNING-12 — `precompute.py` passes `embedding_dim` as a parameter throughout — will write `1024` in metadata

| Field | Value |
|-------|-------|
| **File** | `precompute.py` lines 66, 183, 261, 284, 295, 331 |
| **Problem** | Even though `_flush_batch` does a runtime resize, the final metadata at line 331 records whatever `embedding_dim` was set to at the start. If the default constant (CRITICAL-3) isn't changed, the cache metadata on disk will falsely claim `embedding_dim=1024` even though the actual `.npy` files contain 384-dim vectors |

---

## ℹ️ INFO — Harmless references / historical

| # | File | Line | Content | Notes |
|---|------|------|---------|-------|
| 1 | `backend/app/main.py` | 39, 135, 170 etc. | `/ (1024 * 1024)` | Bytes → MB conversion — not embedding dimension |
| 2 | `backend/app/api/v1/endpoints/platform.py` | 1344, 1565 etc. | `/ (1024 * 1024)`, `/ 1024` | Bytes → MB/KB conversions |
| 3 | `backend/app/services/model_service.py` | 79, 89, 90 | `/ (1024 * 1024)` | Memory unit conversions |
| 4 | `backend/app/services/job_manager.py` | 203 | `/ (1024 * 1024)` | Memory unit conversion |
| 5 | `backend/scripts/migrate_to_supabase.py` | 109, 110 | `25 * 1024 * 1024` | File size check — not embedding dimension |
| 6 | `backend/app/main.py` | 310 | `float(line.split()[1]) / 1024` | VmHWM KB→MB conversion |
| 7 | `src/features/embedding.py` | 144–147 | `embedding_dim` property docstring: "384 for MiniLM-L6" | Docstring is stale (should say "384 for bge-small") but functionally correct since property calls `get_sentence_embedding_dimension()` dynamically |
| 8 | `src/intelligence/embeddings.py` | 29 | `return int(self._encoder.embedding_dim)` | Dynamic — reads actual model dim, no hardcode |
| 9 | `backend/app/api/v1/endpoints/platform.py` | 1280, 1433 etc. | `encoder.embedding_dim` | Dynamic reads — not hardcoded |
| 10 | `backend/app/services/model_service.py` | 236–244 | `dim = loaded.get_sentence_embedding_dimension()` | Dynamic — correct |
| 11 | `EmbeddingAudit.md` | 30 | "Embedding Dimension: 1024" | Historical doc — harmless |
| 12 | `PerformanceComparison.md` | throughout | References `dim=1024` for bge-large | Historical comparison doc |
| 13 | `ModelLifecycle.md` | throughout | "bge-large" references | Historical doc |
| 14 | `RootCauseReport.md` | throughout | "bge-large" references | Historical doc |
| 15 | `WorkerCrashReport.md` | throughout | "bge-large", "bge-base" model size comparison table | Historical doc with context |
| 16 | `precompute.py` | 90 | `actual_dim = encoded.shape[1]` check | Runtime guard — partially mitigates CRITICAL-3 |
| 17 | `src/ranking/engine.py` | 408 | `dim = passed_embs.shape[1] if ... else self.encoder.embedding_dim` | Dynamic — reads actual dim |
| 18 | `config/ranking_config.yaml` | 8 | `fallback_model: "BAAI/bge-small-en-v1.5"` | Fallback is correct (bge-small) — only primary model is wrong |

---

## Migration Completeness Assessment

### What IS correctly updated to bge-small (384-dim)

| Component | Status |
|-----------|--------|
| `backend/Dockerfile` | ✅ Downloads `bge-small-en-v1.5` |
| `backend/app/core/config.py` | ✅ Default = `bge-small-en-v1.5` |
| `backend/app/services/model_service.py` `_DEFAULT_MODEL` | ✅ `bge-small-en-v1.5` |
| `backend/app/api/v1/endpoints/platform.py` indexing pipeline | ✅ Reads from `settings.embedding_model` — inherits correct value |
| Runtime FAISS build | ✅ `IndexFlatIP(dim)` — dim derived dynamically from encoder |

### What is NOT updated (broken)

| Component | Status |
|-----------|--------|
| `src/features/embedding.py` default | ❌ Still `bge-base-en-v1.5` |
| `config/ranking_config.yaml` | ❌ Still `bge-large-en-v1.5` |
| `precompute.py` `EMBEDDING_DIM_DEFAULT` | ❌ Still `1024` |
| `src/ranking/engine.py` auto-correction block | ❌ Can reload `bge-large` at runtime |
| `tests/test_embedding.py` | ❌ Asserts dim == 1024 (will fail) |
| `tests/test_candidate_metadata_mapping.py` MockEncoder | ❌ Defaults to dim=1024 |

---

## Highest Priority Finding

> **CRITICAL-4 in `src/ranking/engine.py` is the most dangerous issue currently.**
>
> The "dimension auto-correction" block (`if dim == 1024: self.encoder.model_name = "BAAI/bge-large-en-v1.5"`) can silently reload the 1.34 GB `bge-large` model at runtime if any old FAISS index (built with the previous 1024-dim pipeline) is retrieved from Supabase Storage. This would cause an OOM kill on Render — the exact symptom reported. **This block must be removed immediately.**
>
> Additionally, if any project in Supabase still has FAISS index files built with 1024-dim embeddings, attempting to analyze those projects will either trigger the auto-correction (→ OOM) or fail with a FAISS shape mismatch error. Those projects need to be re-indexed.

---

## Confidence Score: 42 / 100

The migration to `bge-small-en-v1.5` is approximately 42% complete. The three production-critical files (Dockerfile, config.py, model_service.py) have been updated, which prevents OOM on fresh deployments. However, six critical assumptions from the old 1024-dim pipeline remain in the codebase and can cause runtime failures.
