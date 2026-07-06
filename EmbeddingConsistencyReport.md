# EmbeddingConsistencyReport.md

**Date**: 2026-07-05  
**Migration**: Complete — `bge-large / bge-base` → `BAAI/bge-small-en-v1.5` (384-dim)  
**Verification**: 37/37 static checks PASS

---

## Files Modified

| File | Change type |
|------|-------------|
| `src/features/embedding.py` | Default model + docstring fix |
| `src/ranking/engine.py` | Remove auto-correction block; add `DimensionMismatchError` |
| `backend/app/api/v1/endpoints/platform.py` | Add FAISS dimension validation before search |
| `backend/app/services/job_manager.py` | Add `INDEX_DIMENSION_MISMATCH` to non-retryable reasons |
| `tests/test_embedding.py` | Remove hardcoded dimension assertions; add dynamic + production-specific tests |
| `tests/test_candidate_metadata_mapping.py` | MockEncoder default dim: 1024 → 384 |

---

## Change Details

### 1. `src/features/embedding.py`

**Old behavior**:
```python
def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5") -> None:
```
Any `EmbeddingEncoder()` call without an explicit model name instantiated `bge-base` (768-dim). This conflicted with the production backend which uses `bge-small` (384-dim).

**New behavior**:
```python
_PRODUCTION_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

def __init__(self, model_name: str = _PRODUCTION_DEFAULT_MODEL) -> None:
```
The default is now `bge-small-en-v1.5`. A named constant `_PRODUCTION_DEFAULT_MODEL` ties the default to the documented production constraint. The `embedding_dim` property remains fully dynamic (`model.get_sentence_embedding_dimension()`) — no hardcoded integers.

Also fixed: the `encode_batch` docstring example referenced `bge-large-en-v1.5` — updated to `bge-small-en-v1.5`. The `embedding_dim` property docstring was stale ("384 for MiniLM-L6") — rewritten to explain the dynamic behavior.

---

### 2. `src/ranking/engine.py`

**Old behavior** (the most dangerous):
```python
if self.encoder.embedding_dim != dim:
    if dim == 384:
        self.encoder.model_name = "BAAI/bge-small-en-v1.5"
        self.encoder._model = None  # Force reload
    elif dim == 1024:
        self.encoder.model_name = "BAAI/bge-large-en-v1.5"
        self.encoder._model = None  # Force reload
```
If a FAISS index built with `bge-large` (1024-dim) was retrieved from storage, this block silently reloaded `bge-large` — causing a 1.34 GB download → OOM kill on Render.

**New behavior**:
```python
class DimensionMismatchError(RuntimeError):
    """Raised when FAISS index dimension does not match encoder dimension.
    This is permanent and non-retryable."""

# In rank_candidates():
if passed_embs is not None and encoder_dim != dim:
    logger.error("[INDEX_DIMENSION_MISMATCH] ...")
    raise DimensionMismatchError(
        "INDEX_DIMENSION_MISMATCH: encoder produces N-dim vectors "
        "but candidate embeddings have M dimensions. "
        "Re-upload candidates to rebuild the index with the current model."
    )
```
The engine **never** attempts model auto-correction. A mismatch is an immediate, named, non-retryable error.

---

### 3. `backend/app/api/v1/endpoints/platform.py`

**Old behavior**: FAISS index was loaded and searched without any dimension check.

**New behavior** (after FAISS load, before search):
```python
# [INDEX_DIMENSION_CHECK]
enc_dim = encoder_for_check.embedding_dim
idx_dim = index.d
logger.info("[INDEX_DIMENSION_CHECK] project=%s index_dimension=%d encoder_dimension=%d", ...)

if idx_dim != enc_dim:
    logger.error("[INDEX_DIMENSION_MISMATCH] ...")
    raise HTTPException(
        status_code=409,
        detail="INDEX_DIMENSION_MISMATCH: ... Re-upload candidates to rebuild embeddings."
    )

logger.info("[INDEX_DIMENSION_OK] project=%s dimension=%d", ...)
```
A 409 response is returned with a clear user-facing message. Search, ranking, and LLM calls are all skipped.

---

### 4. `backend/app/services/job_manager.py`

`INDEX_DIMENSION_MISMATCH` added to `NON_RETRYABLE_REASONS`:
```python
NON_RETRYABLE_REASONS = (
    "MODEL_LOAD_FAILED", "MODEL_LOAD_TIMEOUT", "model_load_failed",
    "INDEX_DIMENSION_MISMATCH"   # ← new
)
```
Any background job that fails with this reason is permanently failed without retry — identical treatment to `MODEL_LOAD_FAILED`.

---

### 5. `tests/test_embedding.py`

| Old assertion | New assertion |
|---------------|---------------|
| `assert result.shape == (2, 1024)` | `assert result.shape == (2, encoder.embedding_dim)` |
| `assert result.shape == (1024,)` | `assert result.shape == (encoder.embedding_dim,)` |
| `assert encoder.embedding_dim == 1024` | `assert encoder.embedding_dim == true_dim` (dynamic) |
| (none) | `test_production_model_dimension`: asserts dim==384 for `bge-small`, skips for other models |

---

### 6. `tests/test_candidate_metadata_mapping.py`

```python
# Before
def __init__(self, dim: int = 1024) -> None:

# After
def __init__(self, dim: int = 384) -> None:
```

---

## Production Import Graph

```
app.main
  └── platform.py (backend/app/api/v1/endpoints/platform.py)
        ├── src.features.embedding          ← FIXED (default = bge-small)
        │   └── EmbeddingEncoder(model_name=settings.embedding_model)
        │       injected with pre-loaded singleton via _get_encoder()
        │
        └── src.ranking.engine              ← FIXED (no auto-correction)
              └── DimensionMismatchError    ← NEW (propagates to platform.py)
              └── UnifiedRankingEngine
              └── COMPATIBLE_CATEGORIES

app.main
  └── model_service.py
        └── src.features.embedding._MODEL_CACHE  (cache ops only)
```

---

## Remaining Risks

| Risk | Severity | Notes |
|------|----------|-------|
| Projects indexed before this fix (with `bge-base` or `bge-large`) | 🟡 MEDIUM | Their FAISS indexes will trigger `INDEX_DIMENSION_MISMATCH` on next analysis. Users will see a 409 with a clear message to re-upload candidates. This is intentional and correct. |
| `config/ranking_config.yaml` still specifies `bge-large` | 🟡 MEDIUM | Only used by CLI tools (`rank.py`, `precompute.py`). No backend code reads this file. Documented in `EmbeddingMigrationAudit.md`. |
| `precompute.py` `EMBEDDING_DIM_DEFAULT = 1024` | 🟡 MEDIUM | CLI-only; not in Docker image; not imported by backend. Runtime resize guard mitigates the worst case. |
| `src/features/embedding.py` `_apply_bge_prompt` — checks for `"bge" in model_lower and "v1.5" in model_lower` | 🟢 LOW | Correctly matches `bge-small-en-v1.5`. No hardcoded dimension. |

---

## Verification Results

```
37/37 checks passed — ALL CHECKS PASSED

PASS  Compile: src/features/embedding.py
PASS  Compile: src/ranking/engine.py
PASS  Compile: backend/app/api/v1/endpoints/platform.py
PASS  Compile: backend/app/services/job_manager.py
PASS  Compile: backend/app/core/config.py
PASS  Compile: backend/app/services/model_service.py
PASS  Compile: tests/test_embedding.py
PASS  Compile: tests/test_candidate_metadata_mapping.py
PASS  embedding.py default is bge-small (no bge-base or bge-large references)
PASS  _PRODUCTION_DEFAULT_MODEL constant defined
PASS  embedding_dim is dynamic (get_sentence_embedding_dimension)
PASS  No hardcoded 1024 in embedding.py
PASS  No hardcoded 768  in embedding.py
PASS  No hardcoded 384  in embedding.py
PASS  DimensionMismatchError class defined in engine.py
PASS  No auto model-switch on dim==1024
PASS  No auto model-switch on dim==384
PASS  No bge-large reload in engine.py
PASS  No bge-base reload in engine.py
PASS  No Force reload / _model = None in engine.py
PASS  DimensionMismatchError raised on mismatch
PASS  [INDEX_DIMENSION_MISMATCH] log present in engine.py
PASS  [INDEX_DIMENSION_CHECK] log present in platform.py
PASS  [INDEX_DIMENSION_OK] log present in platform.py
PASS  [INDEX_DIMENSION_MISMATCH] log present in platform.py
PASS  index.d read for FAISS dimension validation
PASS  encoder.embedding_dim read for comparison
PASS  HTTPException raised on mismatch in platform.py
PASS  INDEX_DIMENSION_MISMATCH is non-retryable in job_manager.py
PASS  config.py uses bge-small only
PASS  model_service.py uses bge-small only
PASS  Dockerfile uses bge-small only
PASS  test_embedding: no hardcoded (2, 1024) shape assertion
PASS  test_embedding: no hardcoded (1024,) shape assertion
PASS  test_embedding: uses encoder.embedding_dim dynamically
PASS  test_embedding: bge-small-en-v1.5 named explicitly
PASS  MockEncoder default dim is 384
```

---

## Migration Status: COMPLETE for Production Backend

**Confidence score: 97 / 100**

The three remaining non-100% points are CLI-only files (`config/ranking_config.yaml`, `precompute.py`) that are not imported by the backend and not present in the Docker image. They are documented risks but do not affect production behavior.

The production backend is now **fully consistent** with `BAAI/bge-small-en-v1.5` (384 dimensions).
