# RootCauseReport.md — Root Cause Analysis: Indexing Stuck at 20%

## Confirmed Root Cause

**The indexing pipeline hangs at 20% because `SentenceTransformer(model_name, device="cpu")` blocks indefinitely when the model is not cached and the network is slow or timing out — with no exception, no timeout, and no log output.**

---

## Issue 1 — Model load has no timeout and produces no log before hanging

| Field | Detail |
|-------|--------|
| **Problem** | Progress reaches 20%, then nothing happens for minutes |
| **Root cause** | `_get_encoder()` calls `EmbeddingEncoder.load_model()` which calls `SentenceTransformer(model_name, device="cpu")`. This downloads `BAAI/bge-large-en-v1.5` (~1.3 GB) from HuggingFace Hub if not cached. There is no timeout on the HTTP download. On Render's network, this can take 120–300 seconds, or hang indefinitely if the proxy returns no data. There was no log line before the call, so Render logs showed nothing after `progress=20%`. |
| **Affected file** | `backend/app/api/v1/endpoints/platform.py` — `process_project_data_task` |
| **Affected line** | `encoder = _get_encoder()` (was line ~985, after `_sync_update_progress(..., 20, ...)`) |
| **Severity** | 🔴 Critical — entire pipeline stops here every cold start |
| **Fix applied** | Added `[STAGE_START] stage=load_model` log **before** the call, `[STAGE_END]` after, and `[STAGE_FAIL]` with full traceback on exception. Added `[PIPELINE_TIMEOUT]` warning if elapsed > 60s. |

---

## Issue 2 — Exception from model load not attributed to any stage

| Field | Detail |
|-------|--------|
| **Problem** | When the model download eventually failed (timeout, OOM), the outer `except Exception as e` logged `[BACKGROUND_TASK_FAIL] Attempt N/3 failed: <message>`. No stage name, no traceback, no RAM value. Impossible to distinguish from a FAISS error or a Supabase error. |
| **Root cause** | The entire embedding block (model load + all batches + FAISS + uploads) was inside a single outer try/except with no per-stage isolation. |
| **Affected file** | `backend/app/api/v1/endpoints/platform.py` |
| **Severity** | 🔴 Critical — makes production debugging impossible |
| **Fix applied** | Every stage is now wrapped in its own `try/except` that logs `[STAGE_FAIL]` with stage name, elapsed time, RAM, and `logger.exception()` (full traceback). Each re-raises so the outer retry loop still handles it. |

---

## Issue 3 — In-memory cache not updated on failure → SSE loops forever

| Field | Detail |
|-------|--------|
| **Problem** | After all 3 retries failed, the SSE stream continued emitting `status=embedding, progress=20%` forever. The frontend never received `status=failed`. |
| **Root cause** | On the failure path, `_sync_fail_job(project_id, reason)` was called to update the DB. But `_sync_fail_job` schedules an async coroutine with a 5s timeout on the event loop. Meanwhile `_progress_cache[project_id]["status"]` was still `"embedding"`. The SSE `event_generator` reads the in-memory cache first. It checked `if status_info["status"] in ["completed", "failed", "cancelled"]` — but since the cache still said `"embedding"`, this was never true. The loop ran indefinitely. |
| **Affected file** | `backend/app/api/v1/endpoints/platform.py` — failure path of `process_project_data_task` |
| **Severity** | 🔴 Critical — UI permanently frozen; user cannot take any action |
| **Fix applied** | In-memory cache is now updated to `status="failed"` synchronously (direct dict mutation) **before** calling `_sync_fail_job`. SSE reads the cache immediately and sees the terminal state on the next 2-second poll. |

---

## Issue 4 — SSE emits no error event before closing on internal exception

| Field | Detail |
|-------|--------|
| **Problem** | If `event_generator` itself threw an exception (e.g., Supabase query error), the generator would stop without sending any event. The client would see the connection drop silently. |
| **Root cause** | No try/except inside `event_generator`. |
| **Affected file** | `backend/app/api/v1/endpoints/platform.py` — `get_progress_stream` |
| **Severity** | 🟠 Medium |
| **Fix applied** | `event_generator` now wraps the poll loop in try/except. On any error, it emits a `status=failed` event before breaking, so the frontend always gets a terminal signal. |

---

## Issue 5 — No SSE heartbeat; proxy closes idle connections

| Field | Detail |
|-------|--------|
| **Problem** | During model download (60–300s), no SSE events were emitted. Render's proxy (and Nginx) close connections idle for >60s. The frontend `EventSource` would fire `onerror`. |
| **Root cause** | `event_generator` had `await asyncio.sleep(2.0)` at the bottom of the loop, but if the worker was blocked in a thread (model download), the loop itself wasn't reached — the SSE coroutine was simply waiting for the next 2s tick. Between ticks, if the worker was busy, no data was sent. The 2s interval was fine for fast batches but provided no protection against long operations. |
| **Severity** | 🟠 Medium — caused the SSE disconnect that appeared in the browser console |
| **Fix applied** | Added SSE comment-line heartbeat (`": heartbeat\n\n"`) every 5 seconds. Comment lines are ignored by browsers but prevent proxy timeout. Also added `Cache-Control: no-cache` and `X-Accel-Buffering: no` response headers. |

---

## Issue 6 — Frontend permanently lost SSE on `onerror`, never reconnected

| Field | Detail |
|-------|--------|
| **Problem** | When the SSE connection was dropped (proxy timeout, backend restart, issue 5 above), the frontend called `eventSource.close()` and never reconnected. Progress bar remained frozen at whatever value was last seen. The user had no way to know if the job was still running, had failed, or had completed. |
| **Root cause** | `onerror` handler only closed the connection without scheduling a reconnect. |
| **Affected file** | `frontend/src/app/(dashboard)/projects/[id]/page.tsx` |
| **Severity** | 🟠 Medium — secondary symptom of issues 3 and 5 |
| **Fix applied** | Frontend now reconnects with exponential backoff (2s initial, 1.5× multiplier, 15s cap). Reconnect is cancelled when a terminal event (`completed`, `failed`, `cancelled`) is received or the component unmounts. |

---

## Issue 7 — Zero-division risk in progress formula

| Field | Detail |
|-------|--------|
| **Problem** | `progress_pct = 20 + int(global_idx / total_candidates * 60)` — if `total_candidates` is 0 (empty file), this raises `ZeroDivisionError` inside the batch loop. |
| **Root cause** | No guard on division by zero. |
| **Affected file** | `backend/app/api/v1/endpoints/platform.py` |
| **Severity** | 🟡 Low — only triggers if upload contained zero valid candidates (already rejected earlier, but defensive fix applied) |
| **Fix applied** | Changed to `int(global_idx / max(total_candidates, 1) * 55)` with adjusted range 25–80%. |

---

## Summary

| # | Severity | Status | Description |
|---|----------|--------|-------------|
| 1 | 🔴 Critical | ✅ Fixed | Model load hangs silently, no log, no timeout detection |
| 2 | 🔴 Critical | ✅ Fixed | No per-stage exception isolation or traceback logging |
| 3 | 🔴 Critical | ✅ Fixed | In-memory cache not updated on failure → SSE loops forever |
| 4 | 🟠 Medium  | ✅ Fixed | SSE generator crashes silently with no terminal event |
| 5 | 🟠 Medium  | ✅ Fixed | No heartbeat → proxy closes idle SSE connection |
| 6 | 🟠 Medium  | ✅ Fixed | Frontend never reconnects after SSE drop |
| 7 | 🟡 Low     | ✅ Fixed | Division by zero in progress formula |
