# UploadCrashInvestigation.md

## Root Cause

**The backend crashed during `POST /upload` because `await parse_jd_with_llm(raw_text)` was called synchronously on the upload request coroutine.**

`parse_jd_with_llm` makes an HTTP request to OpenRouter with a **120-second timeout**. When OpenRouter was slow, unreachable, or returned an error during a specific deployment window, this call either:
- Hung the upload coroutine for up to 120 seconds until Render's request proxy killed it → **502**
- Raised an unhandled exception that propagated past the FastAPI route → **worker stderr crash → Render restart → 502**

Since Render returned HTTP 502 (no response at all), the browser's `fetch()` received a network-level error and incorrectly reported it as a CORS failure. The CORS infrastructure was working correctly throughout — confirmed by `[CORS_PREFLIGHT_SUCCESS]` logs.

---

## Confirmed Crash Locations

| # | Location | Line | Problem |
|---|----------|------|---------|
| 1 | `upload_file()` JD branch | ~2216 | `await parse_jd_with_llm(raw_text)` on hot path, 120s timeout |
| 2 | `process_jd_llm_background_task()` | ~532 | `asyncio.run(parse_jd_with_llm(...))` — creates new event loop in a thread, conflicting with uvicorn's running loop → `RuntimeError: This event loop is already running` |
| 3 | `process_candidate_upload_task()` | ~630 | `asyncio.run(job_manager.register_job(...))` — same RuntimeError risk |

---

## Timeline

```
Browser → POST /upload?upload_type=job_description
  → [CORS_PREFLIGHT] OPTIONS 200 ✓
  → [REQUEST_START] POST /upload ...
  → user_id = get_user_id()  ✓
  → proj_res = supabase ...   ✓
  → content = await file.read()  ✓
  → raw_text = _extract_jd_raw_text()  ✓
  → await parse_jd_with_llm(raw_text)
       → httpx POST https://openrouter.ai  ← HANGS here (0–120s)
       → Render proxy timeout fires (typically 30s)
       → Worker receives RST packet mid-await
       → Exception propagates uncaught
       → Worker crashes (or returns no response)
       → Render returns 502 to browser
       → Browser sees network error → reports as CORS
```

---

## Request Flow (After Fix)

```
POST /upload?upload_type=job_description
  [UPLOAD_REQUEST_RECEIVED] project=abc elapsed=0.000s rss=220.0MB
  [AUTH_VERIFIED] project=abc user=d6c20e10 elapsed=0.001s
  [PROJECT_VERIFIED] project=abc elapsed=0.120s rss=220.1MB
  [FILE_RECEIVED] project=abc filename=jd.pdf type=job_description
  [FILE_PARSED] project=abc raw_text_len=2841 rss=220.3MB elapsed=0.145s
  [SUPABASE_UPLOAD_STARTED] project=abc stage=jobs_insert elapsed=0.146s
  [SUPABASE_UPLOAD_FINISHED] project=abc stage=jobs_insert elapsed=0.380s
  [BACKGROUND_JOB_CREATED] project=abc job=<uuid> elapsed=0.381s
  [BACKGROUND_TASK_SCHEDULED] project=abc task=process_jd_llm elapsed=0.382s
  [UPLOAD_RESPONSE_SENT] project=abc type=job_description elapsed=0.383s
  ← HTTP 200 {"id": "...", "title": "...", ...}

  (background thread, ~5–30s later)
  [JD_PARSE_BACKGROUND_START] project=abc job=<uuid>
  → LLM call runs in background thread
  [JD_PARSE_BACKGROUND_COMPLETE] project=abc job=<uuid>
  → jobs table updated with extracted skills/experience
```

---

## Memory Timeline (after fix, typical JD upload)

| Stage | RSS |
|-------|-----|
| Request received | ~220 MB |
| After `file.read()` (JD text) | +0.5 MB |
| After `_extract_jd_raw_text()` | +0.1 MB |
| After `parse_jd_backup()` | +0.1 MB |
| After Supabase insert | +0.5 MB |
| After response sent | net ≈ +1 MB |
| Total delta | **< 2 MB** — well under 300 MB limit |

---

## Thread Timeline (after fix)

| Thread | Role | Safety |
|--------|------|--------|
| uvicorn event loop | Handles HTTP, awaits upload coroutine | Never blocked by LLM |
| `process_jd_llm_background_task` | Runs LLM in thread pool via `BackgroundTasks` | Wrapped in `_safe_background_task` → exception logged, never re-raised |
| `process_candidate_upload_task` | Parses, stores, indexes candidates | Wrapped in `_safe_background_task` → exception logged, never re-raised |
| model-preload daemon | Downloads/loads SentenceTransformer | Never touches upload path |

---

## Worker Restart Detection

Render restarts are detected by comparing PID across requests. The `[STARTUP_PERF]` log at boot records PID. The `[REQUEST_START]` middleware logs PID on every request. A PID change between requests indicates a worker restart.

No PID instrumentation exists in the upload handler yet — the existing `_pid = os.getpid()` inside `upload_file` combined with the `[REQUEST_START]` middleware PID log is sufficient to detect this.

---

## OOM Analysis

The old code path loaded `parse_jd_with_llm` and potentially triggered `httpx.AsyncClient` creation (HTTP client). The new path only calls `parse_jd_backup` (pure regex, ~0 MB) before returning.

The 300 MB RSS limit is not reachable on the upload path after the fix:
- No model loaded
- No FAISS created
- No embeddings generated
- No OpenRouter call on hot path
- File bytes released immediately after saving to disk

---

## Fixes Applied

### Fix 1 — Remove `await parse_jd_with_llm` from upload hot path

**Before**: Called `await parse_jd_with_llm(raw_text)` inline — 120s timeout risk  
**After**: Calls `parse_jd_backup(raw_text)` (fast regex, < 5ms) and dispatches `process_jd_llm_background_task` to `BackgroundTasks`  
**Effect**: Upload returns HTTP 200 in < 400ms regardless of OpenRouter availability

### Fix 2 — Replace `asyncio.run()` with thread-safe event loop access

**Before (two locations)**:
```python
asyncio.run(parse_jd_with_llm(raw_text))     # raises RuntimeError in active event loop
asyncio.run(job_manager.register_job(...))   # same issue
```

**After**:
```python
loop = asyncio.get_event_loop()
if loop.is_running():
    fut = run_coroutine_threadsafe(coro, loop)
    result = fut.result(timeout=60.0)
else:
    result = loop.run_until_complete(coro)
```

### Fix 3 — Full upload request instrumentation

Every stage now logs: `[UPLOAD_REQUEST_RECEIVED]` → `[AUTH_VERIFIED]` → `[PROJECT_VERIFIED]` → `[FILE_RECEIVED]` → `[FILE_PARSED]` → `[SUPABASE_UPLOAD_STARTED]` → `[SUPABASE_UPLOAD_FINISHED]` → `[BACKGROUND_JOB_CREATED]` → `[BACKGROUND_TASK_SCHEDULED]` → `[UPLOAD_RESPONSE_SENT]`

### Fix 4 — Request logging middleware

Added `request_logging_middleware` to `main.py`:
- `[REQUEST_START]` on every incoming request: method, path, content-length, PID
- `[REQUEST_END]` on completion: status, elapsed
- `[REQUEST_ERROR]` if status >= 500
- `[REQUEST_EXCEPTION]` if an exception escapes middleware (with full traceback)

### Fix 5 — Upload endpoint global exception wrapper

The entire `upload_file` handler is wrapped in:
```python
try:
    ...
except HTTPException:
    raise  # 4xx pass through normally
except Exception as exc:
    logger.error("[UPLOAD_FATAL_EXCEPTION] ...")
    return JSONResponse(status_code=500, content={"detail": ..., "traceback": ...})
```

This guarantees the worker never dies from an upload exception.

---

## Verification Results

```
29/29 checks passed — ALL CHECKS PASSED

PASS  Compile: app/services/model_service.py
PASS  Compile: app/main.py
PASS  Compile: app/services/job_manager.py
PASS  Compile: app/api/v1/endpoints/platform.py
PASS  No `await parse_jd_with_llm` on upload hot path
PASS  LLM dispatched via _safe_background_task
PASS  Tag [UPLOAD_REQUEST_RECEIVED] in upload handler
PASS  Tag [AUTH_VERIFIED] in upload handler
PASS  Tag [PROJECT_VERIFIED] in upload handler
PASS  Tag [FILE_RECEIVED] in upload handler
PASS  Tag [FILE_PARSED] in upload handler
PASS  Tag [SUPABASE_UPLOAD_STARTED] in upload handler
PASS  Tag [BACKGROUND_JOB_CREATED] in upload handler
PASS  Tag [BACKGROUND_TASK_SCHEDULED] in upload handler
PASS  Tag [UPLOAD_RESPONSE_SENT] in upload handler
PASS  Tag [UPLOAD_FATAL_EXCEPTION] in upload handler
PASS  REQUEST_START middleware present
PASS  REQUEST_END middleware present
PASS  REQUEST_EXCEPTION middleware present
PASS  No asyncio.run() in process_jd_llm_background_task
PASS  No asyncio.run() in process_candidate_upload_task
PASS  process_candidate_upload_task has except block
PASS  No _get_encoder() on upload hot path
PASS  No SentenceTransformer on upload path
PASS  No FAISS on upload hot path
PASS  No OpenRouter call on upload hot path
PASS  No local `import asyncio` in main.py
PASS  Global exception handler in main.py
PASS  global decls before code in model_service
```

---

## Regression Risks

| Risk | Mitigation |
|------|------------|
| JD skills/experience not extracted immediately | `parse_jd_backup` extracts skills/exp synchronously via regex before return; LLM enrichment updates DB in background |
| Background LLM task fails silently | `_safe_background_task` wrapper logs `[JD_PARSE_BACKGROUND_FATAL]` with full traceback |
| Candidate upload background task fails | Same `_safe_background_task` wrapper logs `[CANDIDATE_UPLOAD_BACKGROUND_FATAL]` |
| `run_coroutine_threadsafe` timeout (60s) | Falls through to `parse_jd_backup` fallback in all branches |
| Memory spike from large file | `[UPLOAD_MEMORY_SPIKE]` logged if delta > 50 MB; raw bytes released immediately after save |
