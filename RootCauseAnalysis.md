# RootCauseAnalysis.md

## Executive Summary

`GET /api/v1/platform/projects` returns HTTP 502. The browser reports a CORS error. **The CORS infrastructure is working correctly.** The 502 is caused by the backend crashing or timing out before sending a response. The browser misreads the TCP-level failure as a CORS rejection.

---

## Root Cause 1 — Blocking synchronous Supabase calls on every GET /projects request (PRIMARY CRASH)

**File**: `backend/app/api/v1/endpoints/platform.py`  
**Functions**: `_enforce_analysis_timeouts()`, `_enforce_embedding_timeouts()`  
**Called from**: `list_projects()` lines 1997–1998, `get_project()` lines 2042–2043

```python
@router.get("/projects")
async def list_projects(current_user: ...):
    _enforce_analysis_timeouts()    # ← Supabase SELECT on every request
    _enforce_embedding_timeouts()   # ← Supabase SELECT on every request
    ...
```

**What happens**:
1. Every `GET /projects` call executes TWO synchronous Supabase queries before the main query.
2. These functions call `supabase_client.table("projects").select(...)` synchronously on the async event loop via the blocking Supabase client.
3. If Supabase is slow (cold connection, network hiccup, query planner timeout), these calls can take 5–30+ seconds.
4. Render's request proxy has a 30-second timeout. If the two enforcement queries + main query exceed 30s total, Render kills the connection → 502.
5. The browser receives no response → incorrectly reports CORS failure.

**Proof from code**: Both functions make `supabase_client.table(...).execute()` calls inside synchronous functions called from an async route handler. These are blocking I/O calls on the uvicorn event loop.

---

## Root Cause 2 — `is_upload_allowed()` permanently returns False (UPLOAD BLOCKED)

**File**: `backend/app/core/startup_state.py`  
**Never called**: `mark_api_ready()`, `mark_startup_check_complete()`, `mark_initialization_complete()`

`startup_state.py` defines three flags. `is_upload_allowed()` requires all three to be `True`. `main.py`'s `lifespan()` calls `run_startup_check()` but **never calls any of the mark functions**. All flags stay `False` forever. `_ensure_upload_service_ready()` in `platform.py` calls `is_upload_allowed()` and raises HTTP 503 on every upload attempt.

**Effect**: All `POST /upload` requests return 503 permanently after deployment.

---

## Root Cause 3 — No per-stage logging or crash-safe wrapper on list_projects

**File**: `backend/app/api/v1/endpoints/platform.py`  
**Function**: `list_projects()`

The original endpoint had no try/except, no stage logging, and no per-request ID. When an exception occurred (Supabase timeout, serialization error), it propagated through FastAPI's global exception handler which returned HTTP 500 — but with no trace of which stage failed.

---

## Why the Browser Reports CORS

The browser sees one of:
- TCP connection reset (Render killed the worker → no HTTP response → browser reports CORS)
- HTTP 502 with no CORS headers (Render's proxy responds without forwarding backend headers)
- HTTP 503 from `_ensure_upload_service_ready()` — the 503 response itself doesn't include CORS headers

In all three cases, the `Access-Control-Allow-Origin` header is missing — not because CORS is misconfigured, but because the backend never sent a proper response.

---

## Module-level Supabase Client Initialization

**File**: `backend/app/api/v1/endpoints/platform.py` line ~131

```python
supabase_client = create_supabase_client(settings.supabase_url, settings.supabase_service_key)
```

This runs at import time. If Supabase credentials are missing or the client constructor raises, the entire `platform.py` module fails to import and ALL platform endpoints return 500. This is a latent risk — not the primary crash cause (since the module compiles and credentials exist in `.env`).

---

## Summary Table

| # | Severity | Root Cause | File | Status |
|---|----------|-----------|------|--------|
| 1 | 🔴 PRIMARY | Blocking Supabase queries on every GET /projects | `platform.py` | ✅ Fixed |
| 2 | 🔴 HIGH | `is_upload_allowed()` always False — uploads blocked | `main.py`, `startup_state.py` | ✅ Fixed |
| 3 | 🟠 MEDIUM | No instrumentation/crash-safe wrapper on list_projects | `platform.py` | ✅ Fixed |
| 4 | 🟡 LOW | Module-level supabase_client init at import time | `platform.py` | Documented (no change needed) |
