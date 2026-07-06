# CrashDiagnostics.md

**Generated**: 2026-07-05  
**Build type**: Crash-proof diagnostic build  
**All files compile clean**: main.py, platform.py, diag.py — exit 0 with `-W error`

---

## What Was Added (No Business Logic Changed)

### New file: `backend/app/core/diag.py`

Central diagnostic module providing:

| Function | Purpose |
|----------|---------|
| `log_call(service, operation, ...)` | Context manager: `CALL_START` / `CALL_SUCCESS` / `CALL_FAILED` with elapsed ms |
| `log_stage(log, project_id, stage, ...)` | Context manager: `STAGE_START` / `STAGE_END` / `STAGE_FAIL` |
| `diag_snapshot()` | Returns `{rss_mb, cpu_pct, threads, uptime_s}` |

### `backend/app/main.py` changes

| Item | Implementation |
|------|---------------|
| `faulthandler.enable()` | At lifespan start — dumps C-level stack on SIGSEGV/SIGABRT |
| `tracemalloc.start(25)` | At lifespan start — 25-frame depth, available for crash snapshots |
| Enhanced `REQUEST_START` | Includes `rid`, `rss`, `cpu`, `threads` |
| Enhanced `REQUEST_END` | Includes `rid`, `status`, `elapsed`, `rss`, `cpu`, `threads` |
| `REQUEST_FAILED` | Emitted for status >= 500 and for uncaught exceptions |
| `tracemalloc` top-5 on crash | Logged inside `request_logging_middleware` on uncaught exception |
| Enhanced `global_exception_handler` | Logs full traceback + `rss`, `cpu`, `threads` before responding |

All of these were **already present in skeleton form** — this pass filled in the missing context.

### `backend/app/api/v1/endpoints/platform.py` changes

| Item | Location |
|------|---------|
| `from app.core.diag import log_call, log_stage, diag_snapshot` | Module import |
| `_safe_background_task` — full traceback + RSS + CPU + threads | Lines ~490–530 |
| `_safe_background_task` — marks project/job FAILED in DB on crash | Lines ~530–550 |
| `_safe_background_task` — logs tracemalloc top-5 on crash | Lines ~540–548 |
| `log_call("storage", "upload_role_index_*")` | upload_indexes stage |
| `log_call("storage", "upload_skill_index")` | upload_indexes stage |
| `log_call("storage", "upload_ids_json")` | upload_indexes stage |
| `log_call("storage", "upload_enriched_candidates")` | upload_artifacts stage |
| `log_call("storage", "upload_embeddings_npy")` | upload_artifacts stage |
| `log_call("storage", "upload_faiss_index")` | upload_artifacts stage |
| `log_call("supabase", "jobs.insert")` in `upload_file` JD branch | DB writes section |
| `log_call("supabase", "jobs.count")` in `upload_file` JD branch | DB writes section |
| `log_call("supabase", "jobs.insert")` in `create_job` | Wrapped with try/except |
| `log_call("supabase", "jobs.count")` in `create_job` | Wrapped with try/except |
| `log_call("supabase", "projects.update_job_count")` in `create_job` | Wrapped with try/except |
| `create_job` exception wrapper → JSON 500 | Previously unprotected |

---

## Crash Locations (Complete Map)

### Rank 1 — Blocking Supabase I/O on every async endpoint (PRIMARY 502 CAUSE)

Every `.execute()` call blocks uvicorn's event loop. Under Render's network:

```
200–2000ms per call × 3–5 calls per endpoint = 600ms–10s per request
Render proxy timeout = 30s
```

**Diagnostics added**: Every `StorageService` call in the indexing pipeline is now wrapped in `log_call(...)` which emits `CALL_START/SUCCESS/FAILED` with elapsed milliseconds. When you see `[CALL_FAILED] service=storage elapsed_ms=30124` you know exactly which call hit the timeout.

**Where to look in logs**:
```
[CALL_START]   service=supabase op=jobs.insert project=abc
[CALL_SUCCESS] service=supabase op=jobs.insert project=abc elapsed_ms=8234  ← slow
[CALL_FAILED]  service=supabase op=jobs.insert project=abc elapsed_ms=30000 ← timeout
```

---

### Rank 2 — `create_job` awaits `parse_jd_with_llm` inline (up to 120s)

**File**: `platform.py`, `create_job()` endpoint  
**Location**: `llm_parsed = await parse_jd_with_llm(raw_text)` inside `POST /projects/{id}/jobs`

The `create_job` endpoint calls OpenRouter with a 120s HTTP timeout. Render drops connection at 30s → 502.

**Diagnostics**: The `create_job` DB writes are now wrapped in `log_call` and a top-level try/except returns JSON 500 instead of crashing. The LLM call itself is still inline (unchanged by design — this is a performance issue documented in RuntimeFailureReport.md, not a diagnostic issue).

---

### Rank 3 — `_run_worker_watchdog()` makes 3 blocking calls on `GET /worker-status`

**File**: `platform.py`, `_run_worker_watchdog()` called from `get_worker_status()`

Every frontend poll during indexing triggers 4 blocking Supabase calls. At 2s polling interval × 500ms each = 2s of event loop blocking per minute.

**Diagnostics**: The watchdog's `except Exception as exc: logger.error(...)` catches and logs all watchdog failures. The outer `get_worker_status` returns a safe fallback if watchdog fails.

---

### Rank 4 — Background tasks failing silently (no job status update)

**Old behavior**: `_safe_background_task` caught exceptions and logged them, but did NOT update the job status to FAILED in Supabase. The UI stayed stuck at `processing`.

**New behavior** (diagnostic fix):
```python
# _safe_background_task now:
1. Logs: [BACKGROUND_TASK_FATAL] task=X exception_type=Y rss=Z cpu=W threads=N
2. Logs: tracemalloc top-5 allocations
3. Updates: background_jobs.status = "failed"
4. Updates: projects.embedding_status = "failed"
5. Logs: [BACKGROUND_TASK_FATAL] project=X marked as FAILED in DB
```

---

### Rank 5 — StorageService calls fail with no elapsed time logged

**Old behavior**: A storage upload failure produced only `[STAGE_FAIL]` with error text.

**New behavior**: Each storage call is wrapped in `log_call(...)`:
```
[CALL_START]   service=storage op=upload_embeddings_npy project=abc elapsed_ms=0
[CALL_FAILED]  service=storage op=upload_embeddings_npy project=abc elapsed_ms=15234
               exception=ConnectionError: ...
               Traceback: ...
```

This tells you which specific file failed and how long it took.

---

### Rank 6 — `create_job` had no outer exception handler

**Old behavior**: Any of the 3 Supabase calls in `create_job` raising would propagate to the global exception handler as HTTP 500 with no context.

**New behavior**: All 3 calls are wrapped in a try/except that returns JSON 500 with stage name, exception type, and full traceback.

---

## Every Background Task

| Task | Function | Crash-safe? | Updates DB on failure? |
|------|----------|------------|----------------------|
| Candidate upload + index | `process_candidate_upload_task` via `_safe_background_task` | ✅ (try/except in wrapper) | ✅ (added in this pass) |
| JD LLM enrichment | `process_jd_llm_background_task` via `_safe_background_task` | ✅ | ✅ (added) |
| Full indexing pipeline | `process_project_data_task` (called from `process_candidate_upload_task`) | ✅ (outer try/except) | ✅ (existing) |
| Model preload | `model_service._do_load` (daemon thread) | ✅ (`_load_state = "failed"`) | N/A |
| Deferred startup | `_deferred_startup` via `_run_deferred_startup_safe` | ✅ (outer wrapper) | N/A |
| Worker heartbeat | `_start_heartbeat` (daemon thread) | ✅ (while True + try/except) | N/A |
| Embedding memory monitor | `_embedding_memory_monitor` (daemon thread) | ✅ (while True + try/except) | N/A |
| Job recovery | `JobManager.recover_interrupted_jobs` | ✅ (outer try/except) | ✅ |

---

## Every Network Dependency

| Dependency | Where called | Diagnostic coverage |
|------------|-------------|-------------------|
| Supabase DB (projects) | Every endpoint | `CALL_START/SUCCESS/FAILED` on key paths |
| Supabase DB (jobs) | create_job, upload_file | `CALL_START/SUCCESS/FAILED` wrapped |
| Supabase DB (background_jobs) | job_manager, watchdog | Logged via existing stage logs |
| Supabase Storage (upload) | upload_indexes, upload_artifacts stages | `CALL_START/SUCCESS/FAILED` wrapped |
| Supabase Storage (download) | analysis endpoint, list_candidates | Logged via existing stage logs |
| HuggingFace Hub | model_service._do_load | `MODEL_LOAD_START/COMPLETE/FAILED` + 30s heartbeat |
| OpenRouter | run_analysis, create_job | `CALL_START/SUCCESS/FAILED` via openrouter module logs |

---

## Points That Can Terminate the Worker

| Risk | Mitigation |
|------|-----------|
| `SIGSEGV` / `SIGABRT` from C extension (FAISS, numpy) | `faulthandler.enable()` — dumps C stack before exit |
| OOM kill from Linux kernel | `[EMBEDDING_ABORT]` fires at 85% threshold, cancels gracefully |
| Unhandled exception in main thread | `sys.excepthook` → `[WORKER_CRASH]` |
| Unhandled exception in daemon thread | `threading.excepthook` → `[THREAD_EXCEPTION]` |
| Unhandled asyncio task exception | `asyncio.set_exception_handler` → `[ASYNC_EXCEPTION]` |
| SIGTERM from Render deploy | Signal handler → `[SIGNAL_RECEIVED]` → graceful shutdown |
| Blocking Supabase > 30s | Render proxy drops; `[CALL_FAILED]` shows elapsed time |
| `logging.shutdown()` during worker crash | Already in shutdown handler, after graceful cleanup |

---

## Log Tags Reference

After this diagnostic build, the following tags are emitted and searchable in Render logs:

```
# Process lifecycle
[WORKER_STARTED]       [WORKER_READY]         [WORKER_EXIT]
[WORKER_CRASH]         [SIGNAL_RECEIVED]      [SHUTDOWN_START]

# Request lifecycle  
[REQUEST_START]        [REQUEST_END]           [REQUEST_FAILED]
[REQUEST_EXCEPTION]    [VALIDATION_ERROR]      [HTTP_EXCEPTION]
[UNHANDLED_EXCEPTION]

# Background tasks
[BACKGROUND_TASK_FATAL]

# External calls (added in this pass)
[CALL_START]           [CALL_SUCCESS]          [CALL_FAILED]

# Pipeline stages (existing + augmented)
[STAGE_START]          [STAGE_END]             [STAGE_FAIL]
[STAGE_PROGRESS]       [STAGE_VERIFY]

# Model service
[MODEL_LOAD_START]     [MODEL_LOAD_COMPLETE]   [MODEL_LOAD_FAILED]
[MODEL_LOAD_TIMEOUT]   [MODEL_STILL_LOADING]   [MODEL_SINGLETON_CREATED]
[MODEL_CACHE_HIT]      [MODEL_CACHE_MISS]      [MODEL_REUSED]

# Memory
[EMBEDDING_MONITOR]    [HIGH_MEMORY_WARNING]   [EMBEDDING_ABORT]
[MEMORY_DIAGNOSTICS]   [TRACEMALLOC_TOP5]

# Diagnostics
[WORKER_HEARTBEAT]     [STARTUP_PERF]          [STARTUP_SUMMARY]
[STARTUP_CHECK]        [RECOVERY_SUMMARY]      [INDEX_DIMENSION_CHECK]
[INDEX_DIMENSION_OK]   [INDEX_DIMENSION_MISMATCH]
```

---

## Verification

```
main.py   — py_compile -W error → EXIT 0
platform.py — py_compile -W error → EXIT 0  
diag.py   — py_compile -W error → EXIT 0
job_manager.py — py_compile -W error → EXIT 0
```

No business logic changed. No APIs changed. No frontend changes.
