# ArchitectureChanges.md

## Summary of Changes

No architecture was redesigned. Four targeted files were modified and one new service file was added.

---

## New File: `backend/app/services/model_service.py`

**Purpose**: Process-wide embedding model singleton.

**Key design decisions**:

- `preload(model_name)` starts a daemon thread immediately and returns — does not block FastAPI startup
- `get_model(timeout=120)` blocks at most `timeout` seconds, emitting heartbeat logs every 5s
- `_do_load()` checks `_MODEL_CACHE` first — logs `[MODEL_CACHE_HIT]` if found, `[MODEL_CACHE_MISS]` if not
- `_lock` (threading.Lock) prevents concurrent load attempts
- `_load_event` (threading.Event) allows all waiters to unblock atomically when load completes
- On timeout: `_load_state = "failed"`, subsequent calls raise `ModelLoadTimeout` immediately — no re-hang
- On exception: `_load_state = "failed"`, `_load_error` stored for error propagation
- Injects loaded model back into `src.features.embedding._MODEL_CACHE` for backward compatibility

---

## Modified: `backend/app/api/v1/endpoints/platform.py`

### `_get_encoder()` — complete rewrite

**Before**: Created `EmbeddingEncoder`, called `load_model()` inline — blocked thread indefinitely.

**After**: Calls `model_service.get_model()`, wraps the result in `EmbeddingEncoder` with `._model` injected. No download ever happens inside the indexing thread.

### `preload_model_singleton()` — new function

Called from `main.py` lifespan before `run_startup_initialization()`. Kicks off the background preload thread.

### Load-model stage exception handler — updated

```python
# Before:
raise  # always re-raised → fell into retry loop

# After:
if isinstance(stage_exc, (ModelLoadTimeout, ModelLoadFailed)):
    _sync_fail_job(project_id, f"MODEL_LOAD_FAILED: {stage_exc}")
    # update in-memory cache to "failed"
    return  # exit immediately — do NOT retry model-load failures
raise  # all other errors still retry
```

---

## Modified: `backend/app/core/config.py`

```python
# Before:
embedding_model: str = "BAAI/bge-large-en-v1.5"  # 1.34 GB

# After:
embedding_model: str = "BAAI/bge-base-en-v1.5"   # 438 MB
```

Model is still fully configurable via `EMBEDDING_MODEL_NAME` or `EMBEDDING_MODEL` env vars.

---

## Modified: `backend/app/main.py`

```python
# Before:
from app.api.v1.endpoints.platform import run_startup_initialization
await run_startup_initialization()

# After:
from app.api.v1.endpoints.platform import run_startup_initialization, preload_model_singleton
preload_model_singleton()   # non-blocking — returns immediately
await run_startup_initialization()
```

The preload starts in a daemon thread the moment the process is ready. By the time a user
uploads candidates (typically 5–30s after deploy), the model is already loading or loaded.

---

## Modified: `backend/app/services/job_manager.py`

### `recover_interrupted_jobs()` — complete rewrite

| Before | After |
|--------|-------|
| Immediately retried on every restart | Waits backoff delay (60s / 120s / 300s) |
| No special handling for model-load failures | `MODEL_LOAD_FAILED` in failure_reason → permanent fail, no retry |
| All retry paths equal | Exponential backoff matching model download time |

### `_safely_run_indexing_with_backoff()` — new method

Wraps `_safely_run_indexing()` with an `asyncio.sleep(delay_seconds)` before starting.
The sleep is non-blocking (event loop free to handle other requests).

---

## Modified: `src/features/embedding.py`

Changed default model name from `bge-large-en-v1.5` to `bge-base-en-v1.5` in `EmbeddingEncoder.__init__`. This ensures standalone use of `EmbeddingEncoder` (CLI scripts, tests) also defaults to the smaller model.

---

## Unchanged

- Database schema — no changes
- Supabase queries — no changes
- API endpoints — no changes  
- Frontend — no changes (SSE reconnect fix from previous session remains)
- FAISS pipeline — no changes
- Analysis endpoint — no changes
- Export endpoint — no changes

---

## Verification Checklist

| Check | Result |
|-------|--------|
| `SentenceTransformer()` only in `model_service.py` | ✅ Confirmed by grep |
| No `EmbeddingEncoder()` instantiation in pipeline | ✅ Only in `_get_encoder()` wrapper |
| No `load_model()` call in backend pipeline | ✅ Confirmed by grep — zero matches |
| `preload_model_singleton` imported and called in `main.py` | ✅ Confirmed |
| Recovery loop has backoff delay | ✅ 60/120/300s |
| Recovery loop doesn't retry `MODEL_LOAD_FAILED` | ✅ `is_non_retryable` check |
| All files pass diagnostics | ✅ No errors |
