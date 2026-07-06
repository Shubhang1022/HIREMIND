# RuntimeFailureReport.md — Runtime Crash Audit

**Date**: 2026-07-05  
**Method**: Static analysis of all execution paths through the 4 target endpoints  
**Scope**: No code modification — audit only

---

## Executive Summary

The HTTP 502 after successful startup has **one primary cause** and four contributing causes:

1. **PRIMARY**: Every async endpoint calls `supabase_client.table(...).execute()` synchronously — this is a **blocking I/O call on the uvicorn event loop**. Under Render's network conditions, a single Supabase round-trip can take 200–2000ms. Multiple calls per request (some endpoints make 3–5) push total latency past Render's 30-second proxy timeout. The connection is dropped. Render returns 502.

2. **CONTRIBUTING**: `get_worker_status` calls `_run_worker_watchdog()` synchronously on every request — this makes 1–3 additional blocking Supabase queries per call.

3. **CONTRIBUTING**: `create_job` can block for up to 120 seconds on `await parse_jd_with_llm(raw_text)` if OpenRouter is slow — this has been partially fixed but the `create_job` endpoint (POST /jobs, not POST /upload) still awaits LLM inline.

4. **CONTRIBUTING**: Module-level `supabase_client = create_supabase_client(...)` at line 131 runs at import time. If Supabase credentials are wrong or the client constructor raises, the entire `platform.py` module fails to import and all endpoints return 500/502.

5. **CONTRIBUTING**: `get_candidate` calls `StorageService.stream_jsonl()` (a blocking I/O operation) directly inside the async handler with no timeout.

---

## Failure Rankings (Most Likely → Least Likely)

### 🔴 RANK 1 — Blocking Supabase I/O in every async endpoint

**Probability**: HIGH (confirmed by Render 502 pattern)  
**Affected endpoints**: ALL  
**Root cause**: The `supabase` Python client (`supabase==2.5.1`) is synchronous. Every `.execute()` call blocks the uvicorn event loop thread for the duration of the network round-trip.

```
GET /api/v1/platform/projects
  supabase_client.table("projects").select("*").eq("user_id", ...).execute()
  ← BLOCKS event loop for 200ms–2000ms
  ← During this time, ALL other requests are queued
  ← If > 30s total: Render proxy drops connection → 502
```

Endpoints and their Supabase call counts:

| Endpoint | Supabase calls | Cumulative risk |
|----------|---------------|----------------|
| `GET /projects` | 1 | Low-Medium |
| `GET /projects/{id}` | 1 | Low |
| `POST /projects` | 2 (dup check + insert) | Medium |
| `GET /worker-status` | 1 + watchdog (2–3 extra) | HIGH |
| `GET /progress-stream` | 1 + per-poll 1 | Medium |
| `POST /upload` (candidates) | 2 (verify + no more inline) | Low |
| `POST /upload` (JD) | 3 (verify + insert + update) | Medium |
| `POST /jobs` (create_job) | 4 + LLM call | HIGH |
| `POST /analyze` | 5+ | CRITICAL |

**Event loop blocking per request** (conservative):
- Single Supabase call: 200–500ms local, 500–2000ms Render network
- `GET /worker-status` makes 3–4 calls → 600ms–8000ms of blocking
- `POST /analyze` makes 5+ calls → easy to exceed 30s total

**Why this causes 502**: Render's proxy has a 30-second idle timeout. The event loop is single-threaded. While one request blocks on Supabase I/O, new requests cannot be dispatched. When the total latency exceeds 30s, Render drops the TCP connection and returns 502.

---

### 🔴 RANK 2 — `create_job` awaits `parse_jd_with_llm` inline (up to 120s)

**Probability**: HIGH if user pastes JD text  
**Affected endpoint**: `POST /api/v1/platform/projects/{id}/jobs`

```python
# create_job() — still on hot path:
try:
    from app.core.openrouter import parse_jd_with_llm
    llm_parsed = await parse_jd_with_llm(raw_text)  # ← 120s HTTP timeout
except Exception:
    ...
```

`parse_jd_with_llm` makes an HTTP request to OpenRouter with a 120-second timeout. If OpenRouter is slow or unreachable, this call blocks the request coroutine for up to 120 seconds. Render's proxy kills it at 30s → 502.

Note: `upload_file()` with `upload_type=job_description` was fixed to use background tasks. But `create_job()` (the `POST /jobs` endpoint for text-paste JD) was **not** fixed — it still awaits LLM inline.

---

### 🔴 RANK 3 — `_run_worker_watchdog()` called synchronously on `GET /worker-status`

**Probability**: HIGH (called on every worker-status poll)  
**Affected endpoint**: `GET /api/v1/platform/projects/{id}/worker-status`

```python
async def get_worker_status(...):
    user_id = get_user_id(current_user)
    _run_worker_watchdog()               # ← synchronous, makes 1–3 Supabase calls
    proj_res = supabase_client.table("projects")...execute()  # ← another blocking call
```

`_run_worker_watchdog()` queries `background_jobs` table and may then update `background_jobs` + `projects` tables — up to 3 blocking calls on the event loop, on every single poll from the frontend.

The frontend polls `worker-status` every 2 seconds during indexing. With 3 blocking calls × 500ms each = 1500ms of event loop blocking per poll.

---

### 🟠 RANK 4 — `get_candidate` uses blocking `StorageService.stream_jsonl()` with no timeout

**Probability**: MEDIUM  
**Affected endpoint**: `GET /api/v1/platform/projects/{id}/candidates/{candidate_id}`

```python
async def get_candidate(...):
    ...
    for c in StorageService.stream_jsonl(bucket, path):  # ← blocking generator, no timeout
        if c.get("candidate_id") == candidate_id:
            return standardize_candidate(c)
    raise HTTPException(status_code=404, ...)
```

`StorageService.stream_jsonl()` is a blocking generator that streams from Supabase Storage. For a large JSONL file with many candidates, this blocks the event loop for the entire scan duration. No timeout. If the file is large or storage is slow, this can easily exceed 30s.

---

### 🟠 RANK 5 — Module-level `supabase_client` creation at import time

**Probability**: MEDIUM (fails silently, manifests as 502 on first request)  
**Affected**: All platform endpoints

```python
# Line 131 of platform.py — runs at IMPORT TIME:
supabase_client = create_supabase_client(settings.supabase_url, settings.supabase_service_key)
```

`create_supabase_client` patches `re.match` using `unittest.mock.patch` to bypass JWT format validation. This runs at module import time. If:
- `settings.supabase_url` is empty → `supabase.create_client("")` may raise
- The `re.match` patch fails (Python version incompatibility) → raises AttributeError
- The `supabase` library version changed its internal structure → raises ImportError

Any of these cause `platform.py` to fail to import → all `@router.*` endpoints are unreachable → FastAPI returns 422 or 500 for every request.

Similarly in `job_manager.py`:
```python
# Line 17 — runs at IMPORT TIME:
supabase_client = create_supabase_client(settings.supabase_url, settings.supabase_service_key)
```

This also runs when `job_manager` is first imported. If `create_supabase_client` raises here, `JobManager` is unavailable.

---

### 🟠 RANK 6 — `list_candidates` streams entire JSONL for every paginated request

**Probability**: MEDIUM  
**Affected endpoint**: `GET /api/v1/platform/projects/{id}/candidates`

```python
async def list_candidates(...):
    for c_raw in StorageService.stream_jsonl(bucket, path):  # ← blocks event loop
        c = standardize_candidate(c_raw)           # ← CPU work
        ...                                         # ← full O(N) scan per page
```

For 10,000+ candidates, this:
1. Blocks the event loop for the entire scan duration
2. Does CPU-intensive `standardize_candidate()` inline in the async handler
3. Is called on every page navigation — no caching

---

### 🟠 RANK 7 — SSE `progress-stream` event loop competition

**Probability**: MEDIUM  
**Affected endpoint**: `GET /api/v1/platform/projects/{id}/progress-stream`

```python
async def event_generator():
    while True:
        ...
        res = supabase_client.table("background_jobs")...execute()  # ← blocking
        ...
        yield f"data: {data_json}\n\n"
        await asyncio.sleep(2.0)
```

The SSE generator runs a blocking Supabase query every 2 seconds for the duration of indexing. For a 2-minute indexing run, this is 60 blocking queries × 200–500ms = 12–30 seconds of total event loop blocking during the SSE stream.

Every other request arriving during this time waits in the event loop queue.

---

### 🟡 RANK 8 — `_run_startup_check()` can block startup if Supabase is slow

**Probability**: LOW-MEDIUM (deferred by 0.5s, but still blocks event loop when it runs)

`run_startup_check()` is now called from `_deferred_startup()` which runs as an asyncio task. But `run_startup_check()` itself calls 3 Supabase `.execute()` calls synchronously — blocking the event loop from within an async task. This can delay the first real request by 1–3 seconds.

---

### 🟡 RANK 9 — `create_job` has no outer exception handler

**Probability**: LOW  
**Affected endpoint**: `POST /api/v1/platform/projects/{id}/jobs`

```python
async def create_job(...):
    ...
    supabase_client.table("jobs").insert(job).execute()         # ← unprotected
    job_count_res = supabase_client.table("jobs")...execute()   # ← unprotected
    supabase_client.table("projects").update(...)...execute()   # ← unprotected
    logger.info("Created job %s...", jid, project_id)
    return job
```

If any of the three Supabase calls raises (network error, constraint violation), the exception propagates to FastAPI's global handler which returns HTTP 500. This is correct behaviour, not a 502. But it means `create_job` has no try/except of its own — unlike `upload_file` which wraps everything.

---

### 🟡 RANK 10 — `update_project` returns `res.data[0]` without checking if data is empty

**Probability**: LOW  
**Affected endpoint**: `PATCH /api/v1/platform/projects/{id}`

```python
res = supabase_client.table("projects").update(update_data)...execute()
return res.data[0]  # ← IndexError if res.data is empty or None
```

If the Supabase update returns empty `data` (e.g. RLS blocked the write, or the row was deleted between the existence check and the update), `res.data[0]` raises `IndexError`. This propagates as HTTP 500.

---

## Call Graphs

### `GET /api/v1/platform/projects`

```
list_projects()
  ├── get_user_id(current_user)              [sync, ~0ms]
  ├── supabase_client.table("projects")      [BLOCKING, 200-2000ms]
  │     .select("*").eq("user_id", ...).execute()
  └── return data                            [always returns]

Exception paths:
  - supabase .execute() network error → propagates to global handler → HTTP 500
  - Any unhandled exception in try block → JSON 500 (wrapped)
  - HTTPException → HTTP 4xx (wrapped)
```

### `POST /api/v1/platform/projects/{id}/upload` (candidates branch)

```
upload_file(upload_type="candidates")
  ├── get_user_id()                          [sync, ~0ms]
  ├── supabase .select("projects").execute() [BLOCKING, 200-2000ms]
  ├── await file.read()                      [async, ~1-100ms]
  ├── temp_raw_path.write_bytes()            [sync disk I/O, ~1ms]
  ├── background_tasks.add_task(            [deferred, instant]
  │     _safe_background_task,
  │     process_candidate_upload_task, ...)
  └── return {"status": "queued", ...}      [always returns]

Background chain (after HTTP response sent):
  process_candidate_upload_task()
    ├── supabase .select("projects")         [BLOCKING in thread]
    ├── stream_candidates()                  [CPU + disk I/O]
    ├── StorageService.upload_file()         [network I/O]
    ├── supabase .insert("candidate_uploads")[BLOCKING]
    ├── supabase .update("projects")         [BLOCKING]
    ├── asyncio event loop (register_job)    [thread-safe]
    └── process_project_data_task()          [full indexing pipeline]
```

### `POST /api/v1/platform/projects/{id}/jobs` (create_job — text JD)

```
create_job()
  ├── get_user_id()                          [sync, ~0ms]
  ├── supabase .select("projects").execute() [BLOCKING]
  ├── await parse_jd_with_llm(raw_text)      [BLOCKING UP TO 120s] ← CRASH RISK
  │   OR parse_jd_backup(raw_text)           [CPU only, fast fallback]
  ├── supabase .insert("jobs").execute()     [BLOCKING, unprotected]
  ├── supabase .select("jobs" count).execute()[BLOCKING, unprotected]
  ├── supabase .update("projects").execute() [BLOCKING, unprotected]
  └── return job                             [always returns if no exception]

⚠ The LLM call (await parse_jd_with_llm) is still inline — not deferred.
```

### `GET /api/v1/platform/projects/{id}/progress-stream`

```
get_progress_stream()
  ├── get_user_id()                          [sync]
  ├── supabase .select("projects").execute() [BLOCKING — on every connect]
  └── StreamingResponse(event_generator())

event_generator() [runs as async generator, held open]
  loop every 2s:
    ├── manager.get_job_status()             [in-memory, instant]
    ├── IF not in cache:
    │     supabase .select("background_jobs").execute() [BLOCKING every 2s]
    ├── yield f"data: {json}\n\n"
    └── await asyncio.sleep(2.0)             [yields event loop, correct]

⚠ Blocking Supabase query every 2s during full indexing (~2 minutes)
⚠ ~60 blocking queries per indexing run
```

---

## Functions Without try/except (Unprotected Paths)

| Function | Unprotected calls | Risk |
|----------|------------------|------|
| `create_job` | 3 Supabase + LLM call | HTTP 500 on any failure |
| `update_project` | `res.data[0]` | IndexError → HTTP 500 |
| `cancel_indexing` | `supabase.update().execute()` after await | Network error → HTTP 500 |
| `list_jobs` | `supabase.select().execute()` | Network error → HTTP 500 |
| `get_progress_stream` inner query | `supabase.select("background_jobs").execute()` | Handled (yields error event) |

---

## Functions That Block the Event Loop

Every `supabase_client.table(...).execute()` call is synchronous and blocks the event loop. This is the single largest architectural risk. The complete list includes:

- `list_projects` (1 call)
- `create_project` (2 calls)
- `get_project` (1 call)
- `update_project` (2 calls)
- `delete_project` (3+ calls)
- `upload_file` JD branch (3 calls)
- `get_worker_status` (1 + watchdog 2–3 calls = 3–4 total)
- `progress-stream` event_generator (1 call every 2s)
- `cancel_indexing` (2 calls)
- `list_candidates` (1 call + streaming storage)
- `get_candidate` (1 call + streaming storage scan)
- `list_jobs` (2 calls)
- `create_job` (4 calls + LLM)
- `run_analysis` (5+ calls)
- `_run_worker_watchdog` (1–3 calls, called inline in get_worker_status)
- `run_startup_check` (5 calls, called in deferred task)

---

## Functions That Can Exceed Render Timeout (30s)

| Function | Max latency | Why |
|----------|------------|-----|
| `create_job` | 120s | `await parse_jd_with_llm` HTTP call |
| `get_worker_status` | 4–8s per call | watchdog + 4 Supabase calls |
| `list_candidates` (large dataset) | 10–60s | Streaming full JSONL from storage |
| `get_candidate` (large dataset) | 10–60s | Sequential scan of full JSONL |
| `run_analysis` | 60–120s | Multiple awaits + FAISS + LLM |
| `progress-stream` | 120s+ total | Held open for full indexing run |

---

## Verification: All Endpoints Return on All Paths

| Endpoint | Returns on every path? | Note |
|----------|----------------------|------|
| `GET /projects` | ✅ Yes (try/except → JSON 500) | |
| `POST /projects` | ⚠ Mostly | `res.data[0]` on dup path can IndexError |
| `GET /projects/{id}` | ✅ Yes | raises HTTPException |
| `PATCH /projects/{id}` | ⚠ Partial | `res.data[0]` can IndexError |
| `DELETE /projects/{id}` | ✅ Yes (204) | |
| `POST /upload` | ✅ Yes (try/except all branches) | |
| `POST /jobs` (create_job) | ⚠ No outer wrapper | exceptions → HTTP 500 from global handler |
| `GET /jobs` | ✅ Yes | |
| `GET /worker-status` | ✅ Yes | |
| `GET /progress-stream` | ✅ Yes (error yielded) | |
| `POST /cancel-indexing` | ✅ Yes | |
| `GET /candidates` | ✅ Yes | |
| `GET /candidates/{id}` | ✅ Yes | |

---

## Recommended Fixes (not applied — audit only)

| Priority | Fix | File | Impact |
|----------|-----|------|--------|
| P0 | Move LLM call off `create_job` hot path (same pattern as upload_file) | platform.py | Eliminates 120s hang |
| P0 | Remove `_run_worker_watchdog()` from `get_worker_status` request path — run it in a periodic background task instead | platform.py | Eliminates 3 extra Supabase calls per poll |
| P0 | Replace `progress-stream` per-event Supabase fallback with in-memory only (skip DB query if cache miss → return "loading") | platform.py | Eliminates 60 blocking queries per indexing run |
| P1 | `list_candidates` / `get_candidate`: add streaming timeout via `asyncio.wait_for` on `asyncio.to_thread(...)` | platform.py | Prevents 30s+ hang on large files |
| P1 | Guard `res.data[0]` with length check in `update_project` and `create_project` | platform.py | Prevents IndexError → HTTP 500 |
| P2 | `run_startup_check` Supabase calls: wrap each in `asyncio.to_thread()` | main.py | Prevents event loop block during deferred startup |
