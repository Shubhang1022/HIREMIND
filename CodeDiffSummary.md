# CodeDiffSummary.md

## Session Summary

All changes focus on making the indexing pipeline complete successfully. No public APIs changed. No frontend changes. No database schema changes.

---

## File 1: `backend/app/api/v1/endpoints/platform.py`

### Change 1 — `_sync_update_progress` signature rewrite (PRIMARY FIX)

**Why**: Callers added `processed_candidates=` and `total_candidates=` keyword arguments that matched `update_job_progress`'s parameter names, but `_sync_update_progress` only accepted the old short names `processed=` and `total=`. This caused `TypeError` before any embedding was generated.

**Before**:
```python
def _sync_update_progress(project_id, stage, progress, status=None,
                          processed=0, total=0, eta="", retry_count=None):
    ...
    coro = manager.update_job_progress(project_id, stage, progress, status,
                                       processed, total, eta, retry_count)
```

**After**:
```python
def _sync_update_progress(
    project_id, stage, progress, status=None,
    processed=0, total=0, eta="", retry_count=None,
    processed_candidates=None,   # new — keyword alias
    total_candidates=None,       # new — keyword alias
    batch=None,                  # new — future use
    total_batches=None,          # new — future use
    elapsed_seconds=None,        # new — future use
    eta_seconds=None,            # new — future use
    memory_usage=None,           # new — future use
    speed=None,                  # new — future use
    **_ignored_kwargs,           # absorb unknown future fields
):
    # keyword form overrides legacy positional form
    resolved_processed = processed_candidates if processed_candidates is not None else processed
    resolved_total     = total_candidates     if total_candidates     is not None else total
    ...
    coro = manager.update_job_progress(project_id, stage, progress, status,
                                       resolved_processed, resolved_total, eta, retry_count)
```

**Compatibility**: All existing callers work unchanged. New callers with `processed_candidates=` now work. Future callers with unknown kwargs won't crash.

---

## File 2: `src/features/embedding.py`

### Change 1 — Default model: `bge-base` → `bge-small`

**Why**: The production default must match the Dockerfile and config.py. Using `bge-base` (768-dim) as default would cause dimension mismatches with indexes built by the backend (which reads from `settings.embedding_model = bge-small`).

```python
# Before
_DEFAULT = "BAAI/bge-base-en-v1.5"

# After
_PRODUCTION_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
```

### Change 2 — Docstring updates

The `encode_batch` docstring example referenced `bge-large-en-v1.5`. Updated to `bge-small-en-v1.5`. The `embedding_dim` property docstring was stale — rewritten to reflect dynamic behavior.

---

## File 3: `src/ranking/engine.py`

### Change 1 — Remove model auto-correction block

**Why**: The block `if dim == 1024: reload bge-large` triggered a 1.34 GB model download → OOM kill on Render. It was the primary reason the worker disappeared.

```python
# DELETED — was the OOM trigger:
if self.encoder.embedding_dim != dim:
    if dim == 384:
        self.encoder.model_name = "BAAI/bge-small-en-v1.5"
        self.encoder._model = None
    elif dim == 1024:
        self.encoder.model_name = "BAAI/bge-large-en-v1.5"
        self.encoder._model = None
```

### Change 2 — Add `DimensionMismatchError`

```python
class DimensionMismatchError(RuntimeError):
    """Raised when FAISS index dimension != encoder dimension.
    Permanent, non-retryable.
    """

# In rank_candidates():
if passed_embs is not None and encoder_dim != dim:
    raise DimensionMismatchError(
        f"INDEX_DIMENSION_MISMATCH: encoder {encoder_dim}-dim vs "
        f"candidates {dim}-dim. Re-upload candidates."
    )
```

---

## File 4: `backend/app/api/v1/endpoints/platform.py` — FAISS dimension check

**Why**: The production analysis endpoint loaded FAISS from storage without verifying it matched the current encoder. A stale index (built with a different model) would cause silent garbage results or trigger the auto-correction (→ OOM).

```python
# After FAISS deserialization, before search:
enc_dim = encoder_for_check.embedding_dim
idx_dim = index.d
logger.info("[INDEX_DIMENSION_CHECK] project=%s index_dimension=%d encoder_dimension=%d",
            project_id, idx_dim, enc_dim)
if idx_dim != enc_dim:
    logger.error("[INDEX_DIMENSION_MISMATCH] ...")
    raise HTTPException(409, "INDEX_DIMENSION_MISMATCH: Re-upload candidates.")
logger.info("[INDEX_DIMENSION_OK] project=%s dimension=%d", project_id, idx_dim)
```

---

## File 5: `backend/app/services/job_manager.py`

### Change — `INDEX_DIMENSION_MISMATCH` added to non-retryable reasons

**Why**: A mismatched index is a data problem, not a transient failure. Retrying will always fail the same way. Treat it like `MODEL_LOAD_FAILED` — permanent failure, no retry.

```python
NON_RETRYABLE_REASONS = (
    "MODEL_LOAD_FAILED", "MODEL_LOAD_TIMEOUT", "model_load_failed",
    "INDEX_DIMENSION_MISMATCH",   # new
)
```

---

## File 6: `backend/app/core/config.py`

```python
# Before
embedding_model: str = "BAAI/bge-base-en-v1.5"

# After
embedding_model: str = "BAAI/bge-small-en-v1.5"
```

---

## File 7: `backend/app/services/model_service.py`

```python
# Before
_DEFAULT_MODEL = "BAAI/bge-base-en-v1.5"

# After
_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
```

---

## File 8: `backend/app/main.py`

### Changes (multiple sessions):

1. `sys.excepthook` → logs `[WORKER_CRASH]` on main-thread crashes
2. `threading.excepthook` → logs `[THREAD_EXCEPTION]` on daemon thread crashes
3. `asyncio.set_exception_handler` → logs `[ASYNC_EXCEPTION]` on task crashes
4. Signal handlers for SIGTERM/SIGINT/SIGQUIT → logs `[SIGNAL_RECEIVED]`
5. 30-second heartbeat daemon → `[WORKER_HEARTBEAT]`
6. `mark_api_ready()`, `mark_startup_check_complete()`, `mark_initialization_complete()` wired into `_deferred_startup()` — so `is_upload_allowed()` returns `True` after startup
7. `run_startup_check()` moved to deferred task — API ready in ~50ms, not 1–3s
8. Duplicate `_startup_time` removed

---

## File 9: `tests/test_embedding.py`

All hardcoded `1024` shape assertions replaced with dynamic `encoder.embedding_dim` reads. Added `test_production_model_dimension` that explicitly asserts dim==384 for `bge-small`.

---

## File 10: `tests/test_candidate_metadata_mapping.py`

`MockEncoder(dim=1024)` → `MockEncoder(dim=384)`.

---

## Files NOT Changed

- All frontend files
- All Supabase migration files
- `src/ranking/assembler.py`, `src/ranking/selector.py`, `src/ranking/reasoning.py`
- `src/features/structured.py`, `src/features/text_builder.py`
- `src/scoring/*`
- `backend/app/core/auth.py`
- `backend/app/middleware/rate_limit.py`
- `backend/app/services/cache_service.py`
- `backend/app/services/storage_provider.py`
- `backend/app/api/v1/endpoints/health.py`
- All `docs/` files
- `precompute.py`, `rank.py`, `run_pipeline.py` (CLI only)
- `config/ranking_config.yaml` (CLI only — backend never reads it)
