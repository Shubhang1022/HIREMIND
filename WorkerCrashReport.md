# WorkerCrashReport.md

## Symptom

Render deploys successfully: "Application startup complete. Uvicorn running on 0.0.0.0:8000". After a variable delay (seconds to minutes), the worker process disappears. Render returns HTTP 502. Browser reports a CORS error. No Python traceback appears in Render logs.

---

## Root Cause — Model Name Mismatch Causes OOM Kill

### The exact sequence

```
1. Dockerfile bakes BAAI/bge-small-en-v1.5 (90 MB) into the image at build time.
2. config.py defaulted to:  embedding_model = "BAAI/bge-base-en-v1.5"   ← 438 MB
3. model_service._DEFAULT_MODEL also defaulted to "BAAI/bge-base-en-v1.5"
4. At runtime, preload_model_singleton() calls _do_load("BAAI/bge-base-en-v1.5")
5. _MODEL_CACHE check: "BAAI/bge-base-en-v1.5" not in cache  → CACHE MISS
   (the cache only holds "BAAI/bge-small-en-v1.5" from the Docker build step)
6. SentenceTransformer("BAAI/bge-base-en-v1.5", device="cpu") starts downloading 438 MB
7. Render free tier RAM: 512 MB. Baseline RSS ~200 MB. 438 MB download pushes process to ~640 MB.
8. Linux OOM killer fires: SIGKILL sent to the uvicorn process
9. No Python traceback — SIGKILL cannot be caught
10. Render sees the process exit → returns HTTP 502
11. Browser has no response body → reports CORS error
```

**This is not a CORS bug. It is an OOM kill caused by a model name mismatch between Dockerfile and config.py.**

---

## Evidence

| File | Value (before fix) | Value (after fix) |
|------|-------------------|-------------------|
| `backend/Dockerfile` | `BAAI/bge-small-en-v1.5` (90 MB, baked in) | `BAAI/bge-small-en-v1.5` (unchanged) |
| `backend/app/core/config.py` | `embedding_model = "BAAI/bge-base-en-v1.5"` ❌ | `embedding_model = "BAAI/bge-small-en-v1.5"` ✅ |
| `backend/app/services/model_service.py` | `_DEFAULT_MODEL = "BAAI/bge-base-en-v1.5"` ❌ | `_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"` ✅ |

---

## Why There Was No Python Traceback

`SIGKILL` (signal 9) cannot be caught, handled, or logged by Python. When the Linux OOM killer fires:

1. Kernel sends SIGKILL to the process
2. Kernel immediately terminates the process — no userland code runs
3. No Python exception is raised
4. No `sys.excepthook` fires
5. No `threading.excepthook` fires
6. Render's process supervisor sees exit code 137 (128 + 9)

Render only shows "worker disappeared" — consistent with OOM kill.

---

## Secondary Findings (also fixed)

### No global exception handlers

Before this fix, unhandled exceptions in daemon threads and asyncio tasks were silently swallowed. Added:

- `sys.excepthook` — catches unhandled exceptions in the main thread → logs `[WORKER_CRASH]`
- `threading.excepthook` — catches unhandled exceptions in any thread → logs `[THREAD_EXCEPTION]`
- `asyncio.set_exception_handler()` — catches unhandled asyncio task failures → logs `[ASYNC_EXCEPTION]`

### `_deferred_startup()` could crash the event loop

`asyncio.create_task(_deferred_startup())` with no exception handler. If any exception escaped the inner try/except blocks, it would be logged as an unhandled task exception and the event loop would continue — but all startup state marks would never be called, leaving `is_upload_allowed()` returning False permanently.

**Fix**: Added outer `_run_deferred_startup_safe()` wrapper with a broad `except Exception` that logs `[WORKER_CRASH]` but never re-raises.

### No SIGTERM/SIGINT/SIGQUIT handlers

Worker could be killed by Render's health check timeout with no log. Added signal handlers that log `[SIGNAL_RECEIVED]` before re-raising as `KeyboardInterrupt` to run uvicorn's shutdown sequence.

### No heartbeat

No periodic health signal to confirm the worker was alive between requests. Added a 30-second daemon thread that logs `[WORKER_HEARTBEAT]` with RSS, CPU, thread count, pending asyncio tasks, and event loop state.

---

## All Fixes Applied

| Fix | File | Change |
|-----|------|--------|
| Model name mismatch | `app/core/config.py` | `bge-base-en-v1.5` → `bge-small-en-v1.5` |
| Model name mismatch | `app/services/model_service.py` | `_DEFAULT_MODEL` → `bge-small-en-v1.5` |
| Dockerfile comment | `Dockerfile` | Added warning comment about model name sync |
| `sys.excepthook` | `app/main.py` | Installed — logs `[WORKER_CRASH]` |
| `threading.excepthook` | `app/main.py` | Installed — logs `[THREAD_EXCEPTION]` |
| asyncio exception handler | `app/main.py` | Installed on event loop — logs `[ASYNC_EXCEPTION]` |
| Signal handlers | `app/main.py` | SIGTERM, SIGINT, SIGQUIT — log `[SIGNAL_RECEIVED]` |
| Worker lifecycle logs | `app/main.py` | `[WORKER_STARTED]`, `[WORKER_READY]`, `[WORKER_EXIT]` |
| Heartbeat thread | `app/main.py` | Every 30s — logs `[WORKER_HEARTBEAT]` |
| `_deferred_startup` safety | `app/main.py` | Outer exception wrapper → never crashes event loop |

---

## Model Size Reference

| Model | Size | Render Free (512 MB) | Recommended |
|-------|------|---------------------|-------------|
| `bge-small-en-v1.5` | 90 MB | ✅ Safe (~290 MB total) | ✅ Default |
| `bge-base-en-v1.5` | 438 MB | ❌ OOM (~638 MB total) | Standard tier only |
| `bge-large-en-v1.5` | 1.34 GB | ❌ OOM immediately | Standard 2 GB+ only |

To use a larger model on Render Standard: set `EMBEDDING_MODEL_NAME=BAAI/bge-base-en-v1.5` in Render environment variables. The Dockerfile pre-download step must also be updated accordingly.

---

## Expected Log Sequence After Fix

```
[WORKER_STARTED] pid=1 loop=<uvicorn event loop>
[STARTUP_PERF] API ready in 0.04s RSS=182.3MB
[WORKER_READY] pid=1 rss=182.5MB uptime=0.05s
[STARTUP_STATE] mark_api_ready() called
[MODEL_SERVICE] [MODEL_CACHE_HIT] name=BAAI/bge-small-en-v1.5  ← KEY: cache hit, no download
[MODEL_SERVICE] [MODEL_SINGLETON_CREATED] name=BAAI/bge-small-en-v1.5
[WORKER_HEARTBEAT] pid=1 rss=272.1MB cpu=2.1% threads=4 pending_tasks=0 loop=running uptime=30s
[WORKER_HEARTBEAT] pid=1 rss=272.3MB ... uptime=60s
... (worker stays alive)
```

---

## Verification Results

```
40/40 checks passed — ALL CHECKS PASSED

PASS  Compile: app/main.py
PASS  Compile: app/api/v1/endpoints/platform.py
PASS  Compile: app/services/model_service.py
PASS  Compile: app/core/config.py
PASS  Compile: app/services/job_manager.py
PASS  sys.excepthook installed
PASS  threading.excepthook installed
PASS  asyncio exception handler set
PASS  Log tag [WORKER_STARTED] present
PASS  Log tag [WORKER_READY] present
PASS  Log tag [WORKER_EXIT] present
PASS  Log tag [WORKER_CRASH] present
PASS  Log tag [THREAD_EXCEPTION] present
PASS  Log tag [ASYNC_EXCEPTION] present
PASS  Log tag [SIGNAL_RECEIVED] present
PASS  SIGTERM handler installed
PASS  SIGINT handler installed
PASS  SIGQUIT handler installed
PASS  _deferred_startup wrapped in outer exception handler
PASS  preload_model_singleton wrapped in try/except in lifespan
PASS  _safe_background_task exists
PASS  process_project_data_task has try/except
PASS  No sys.exit() in main.py / platform.py / model_service.py (all 4 patterns)
PASS  WORKER_HEARTBEAT daemon thread started
PASS  Heartbeat logs RSS/CPU/threads/pending_tasks
PASS  Dockerfile model matches config default (both: BAAI/bge-small-en-v1.5)
PASS  model_service default matches config (both: BAAI/bge-small-en-v1.5)
PASS  No local `import asyncio` in main.py
PASS  global decls before code in model_service
```
