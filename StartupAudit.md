# StartupAudit.md

## Startup Sequence (After Refactor)

```
t=0.00s  FastAPI app object created
t=0.01s  Middleware registered (rate_limit, CORS, request_logging, CORS_preflight)
t=0.02s  lifespan() enters
t=0.02s  asyncio exception handler installed on event loop
t=0.02s  faulthandler.enable()
t=0.02s  tracemalloc.start(25)
t=0.02s  _start_heartbeat(30s) — daemon thread started
t=0.03s  validate_required_env() — local, no I/O
t=0.04s  log_startup_summary() — local, no I/O
t=0.05s  preload_model_singleton() → model_service.preload() → daemon thread started
t=0.05s  asyncio.create_task(_run_deferred_startup_safe()) — scheduled, not running yet
t=0.05s  yield ← SERVER READY, accepting HTTP requests
─────────────────────────────────────────────────────────
t=0.55s  _deferred_startup() starts (after asyncio.sleep(0.5))
t=0.55s  mark_api_ready()
t=0.55s  run_startup_check() — Supabase, Storage, FAISS, imports
t=1.5s   run_startup_initialization() — recovery, timeouts
t=1.5s   mark_startup_check_complete(), mark_initialization_complete()
─────────────────────────────────────────────────────────
t=5-60s  model daemon thread: SentenceTransformer() loads from Docker cache
t=~10s   [MODEL_LOAD_COMPLETE] — model ready for encoding
```

**API is ready at t=0.05s. Model is ready at t=~10s (from Docker cache) or t=~60s (cold download).**

---

## Root Cause (Previous Broken Sequence)

The old `main.py` called `run_startup_check()` **synchronously inside the lifespan** before `yield`. `run_startup_check()` made 5 blocking Supabase queries. On cold Render/Railway start, those 5 queries took 1–5 seconds total. During that time, uvicorn was bound to the port but the lifespan had not yielded — so uvicorn could not dispatch any HTTP requests, including health checks. Railway's deploy health check timed out → deploy marked as failed.

Additionally, SIGTERM raised `KeyboardInterrupt` from the signal handler, which interrupted coroutines mid-execution and corrupted asyncio state. Deferred startup tasks got cancelled with `CancelledError` that was not caught, producing "Task exception was never retrieved" log noise and potentially corrupt startup state.

---

## Changes Made

| File | Change |
|------|--------|
| `backend/app/main.py` | Moved `run_startup_check()` into `_deferred_startup()` (runs after yield) |
| `backend/app/main.py` | SIGTERM/SIGINT handlers no longer raise `KeyboardInterrupt` |
| `backend/app/main.py` | `_run_deferred_startup_safe()` catches `asyncio.CancelledError` cleanly |
| `backend/app/main.py` | `faulthandler.enable()` and `tracemalloc.start()` added at lifespan entry |

---

## Verification Evidence

```
PASS  main.py: preload_model_singleton() called before yield (non-blocking)
PASS  main.py: asyncio.create_task for deferred startup (non-blocking)
PASS  main.py: yield before run_startup_check (API ready immediately)
PASS  main.py: SIGTERM handler does NOT raise KeyboardInterrupt
PASS  main.py: CancelledError handled in _run_deferred_startup_safe
```

---

## Remaining Risks

- Supabase queries in `run_startup_check()` still run synchronously on the event loop when executed in `_deferred_startup`. They are now post-yield so they don't block the health check, but they do briefly block other async requests arriving in the ~500ms window. This is acceptable for a startup check.
- If `_deferred_startup` is cancelled before `mark_api_ready()` runs (e.g. very fast SIGTERM on Render), `is_upload_allowed()` will return `False`. Uploads will return 503 until the process restarts. This is correct behavior — a process that is shutting down should not accept new uploads.
