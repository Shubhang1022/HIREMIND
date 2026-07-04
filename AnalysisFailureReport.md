# AnalysisFailureReport.md — Analysis Pipeline Failure Analysis

## Why Analysis Was Failing (Root Causes)

### Root Cause 1 — Backend crashed on startup (CRITICAL)

**File**: `backend/app/api/v1/endpoints/platform.py`  
**Lines 7–9 (pre-fix)**:
```python
from sqlalchemy.connectors import asyncio   # ← invalid module
# pyrefly: ignore [invalid-syntax]
from __future__ import annotations           # ← MUST be first line
```

`from __future__ import annotations` is a special directive that Python requires to be the **absolute first statement** in a file (excluding docstrings). Placing it after any import causes a `SyntaxError` at parse time. Additionally, `sqlalchemy.connectors.asyncio` does not exist — it's `sqlalchemy.ext.asyncio`.

**Result**: The entire FastAPI backend failed to import `platform.py`, meaning **ALL platform API endpoints were unavailable** (404/500 on every request).

**Fix**: Removed the invalid `sqlalchemy.connectors.asyncio` import and moved `from __future__ import annotations` to be the first line after the docstring.

---

### Root Cause 2 — Analysis blocked by missing `POST /jobs` endpoint

**File**: `backend/app/api/v1/endpoints/platform.py`  
**Issue**: The "Paste / Type JD" flow in the frontend calls `POST /platform/projects/{id}/jobs`. This endpoint did not exist — only `GET /jobs` was registered. The result was a **405 Method Not Allowed**.

Users who pasted a job description manually could never create a job, so `selectedJobId` remained empty, and `handleAnalyze()` returned early with "Select or upload a job description first".

**Fix**: Added `POST /projects/{project_id}/jobs` endpoint (`create_job` handler) with full LLM parsing support.

---

### Root Cause 3 — FAISS unavailable in Docker

**File**: `backend/requirements.txt`  
**Issue**: `faiss-cpu` was completely absent from the requirements file. On Render (Docker deployment), `faiss` would not be installed, causing `import faiss` to fail in `process_project_data_task`. This made embedding/indexing always fail with an `ImportError`, leaving jobs permanently stuck in `processing` or `embedding` state.

**Fix**: Added `faiss-cpu>=1.7.4` to `requirements.txt`.

---

### Root Cause 4 — Background worker thread deadlock

**File**: `backend/app/api/v1/endpoints/platform.py`, function `_sync_update_progress`  
**Pre-fix code**:
```python
run_coroutine_threadsafe(coro, loop).result()  # blocks calling thread forever if loop busy
```

The background worker runs in a thread pool thread. `_sync_update_progress` is called dozens of times during indexing. If the asyncio event loop is under load (handling concurrent requests), `future.result()` with no timeout blocks the background thread indefinitely.

**Result**: Background job gets stuck mid-way, appearing as `processing` or `embedding` forever (until Render kills the container). This matches the reported symptom: "background job status sometimes remains processing".

**Fix**: Added `future.result(timeout=5.0)` and made failure non-fatal — progress update failure is logged and skipped rather than blocking the worker.

---

### Root Cause 5 — Supabase storage stream URL wrong

**File**: `backend/app/services/storage_provider.py`, `SupabaseStorageProvider.download_stream`  
**Pre-fix URL**: `{supabase_url}/storage/v1/object/authenticated/{bucket}/{path}`

The `/authenticated/` path in Supabase Storage is for **anon-key JWT access**. When using the **service role key**, the correct URL is `/storage/v1/object/{bucket}/{path}`. Using the wrong path caused all streaming downloads (candidate file streaming, JSONL reading for analysis) to return 400/401/404 from Supabase Storage.

**Result**: Analysis always failed at the "streaming candidates" phase — no candidates loaded, FAISS search returned 0 results.

**Fix**: Changed URL to `/storage/v1/object/{bucket_id}/{path}` (service-key compatible).

---

## Analysis Pre-flight Guard

The `run_analysis()` endpoint now enforces these guards before starting:

1. Concurrent lock: `project_id in _active_analyses` → 409 Conflict
2. Embedding status: must be `completed` or `ready` (not `queued`, `processing`, `pending`, or `failed`)
3. Job existence: `jobs` table query
4. Upload record existence: `candidate_uploads` with status `COMPLETED`
5. Physical artifact existence: all 5+ storage files checked via `StorageService.file_exists()`

If any guard fails, a specific 409 error is returned explaining exactly what's missing. This prevents silent failures where analysis starts but produces garbage results.

---

## Analysis Timeout Chain

| Condition | Timeout | Action |
|-----------|---------|--------|
| Global analysis timeout | 60 seconds | `HTTPException(504)` |
| LLM call timeout | 60 seconds | Falls back to deterministic ranking |
| Indexing watchdog | 10 minutes stuck | Marks job as `failed` |
| OpenRouter 402 (credits) | Immediate | Falls back to deterministic ranking |
| Memory > 450MB | Immediate | Falls back to metadata-only ranking |
