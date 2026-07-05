# AppliedFixes.md

## Fix 1 — Remove blocking Supabase calls from GET /projects and GET /projects/{id}

**File**: `backend/app/api/v1/endpoints/platform.py`  
**Type**: Performance + crash fix

**Before**:
```python
@router.get("/projects")
async def list_projects(current_user: ...):
    _enforce_analysis_timeouts()    # ← blocking Supabase SELECT per request
    _enforce_embedding_timeouts()   # ← blocking Supabase SELECT per request
    user_id = get_user_id(current_user)
    res = supabase_client.table("projects").select("*").eq("user_id", user_id).execute()
    return res.data
```

**After**:
```python
@router.get("/projects")
async def list_projects(current_user: ...):
    # No per-request enforcement calls — these run at startup only
    # Full instrumentation + crash-safe wrapper added
    try:
        user_id = get_user_id(current_user)
        logger.info("[REQUEST_RECEIVED] ...")
        logger.info("[QUERY_STARTED] ...")
        res = supabase_client.table("projects").select("*").eq("user_id", user_id).execute()
        logger.info("[SUPABASE_RESPONSE] ...")
        logger.info("[RESPONSE_SENT] ...")
        return res.data or []
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[REQUEST_EXCEPTION] ...")
        return JSONResponse(status_code=500, content={"detail": ..., "traceback": ...})
```

Same fix applied to `get_project()`.

**Why**: `_enforce_*` timeouts were already called at startup via `run_startup_initialization()`. Calling them on every request added 2 synchronous Supabase round-trips per `GET /projects`, which under Render's network conditions could cause total request time to exceed the 30s proxy timeout → 502.

---

## Fix 2 — Wire startup_state marks into _deferred_startup()

**File**: `backend/app/main.py`  
**Type**: Logic bug fix — uploads were permanently blocked

**Added to `_deferred_startup()`**:
```python
# After asyncio.sleep(0.5)
from app.core.startup_state import mark_api_ready, mark_startup_check_complete, mark_initialization_complete
mark_api_ready()

# After run_startup_check()
mark_startup_check_complete(ok=startup_ok)   # in finally block

# After run_startup_initialization()
mark_initialization_complete()               # in finally block
```

**Why**: `startup_state.py` exported `is_upload_allowed()` which required `_api_ready AND _startup_check_complete AND _startup_check_ok` — but none of the mark functions were ever called from `main.py`. `_ensure_upload_service_ready()` in `platform.py` called `is_upload_allowed()` and raised HTTP 503 on every upload. This fix ensures all flags are set within ~1 second of startup completing.

---

## Fix 3 — Instrumentation and crash-safe wrapper on list_projects

**File**: `backend/app/api/v1/endpoints/platform.py`  
**Type**: Observability + resilience

Added 7 stage log tags:
- `[REQUEST_RECEIVED]` — with PID, thread ID
- `[AUTH_VERIFIED]` — user ID, elapsed time
- `[USER_RESOLVED]`
- `[QUERY_STARTED]` — elapsed time
- `[SUPABASE_RESPONSE]` — row count, elapsed time
- `[SERIALIZATION]` — row count
- `[RESPONSE_SENT]` — total elapsed time

Added top-level `try/except Exception` that returns JSON 500 instead of propagating exceptions to uvicorn.

---

## Fix 4 — Remove asyncio.run() from background task functions (previously applied)

**File**: `backend/app/api/v1/endpoints/platform.py`  
**Functions**: `process_jd_llm_background_task`, `process_candidate_upload_task`

Replaced `asyncio.run(coro)` with thread-safe event loop access pattern:
```python
loop = asyncio.get_event_loop()
if loop.is_running():
    fut = run_coroutine_threadsafe(coro, loop)
    result = fut.result(timeout=60.0)
else:
    result = loop.run_until_complete(coro)
```

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/main.py` | Added startup_state marks to `_deferred_startup()` |
| `backend/app/api/v1/endpoints/platform.py` | Removed blocking calls from list_projects/get_project; added instrumentation + crash wrapper |
