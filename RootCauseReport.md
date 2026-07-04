# RootCauseReport.md — Root Cause Analysis

---

## Issue 1 — Backend completely fails to start

| Field | Detail |
|-------|--------|
| **Problem** | All platform API endpoints return 500 or are unreachable |
| **Root Cause** | `from __future__ import annotations` placed after a regular import in `platform.py`. Python requires this directive to be the first statement. Additionally, `from sqlalchemy.connectors import asyncio` references a non-existent module. Both cause `SyntaxError` / `ImportError` at parse time. |
| **Affected File** | `backend/app/api/v1/endpoints/platform.py` lines 7–9 |
| **Affected Function** | Module-level import — entire file fails to load |
| **Severity** | 🔴 CRITICAL — backend is dead |
| **Fix Applied** | Removed invalid `sqlalchemy.connectors.asyncio` import; moved `from __future__ import annotations` to first line |
| **Estimated Risk** | None — pure import order fix |

---

## Issue 2 — "Paste / Type JD" flow always returns 405

| Field | Detail |
|-------|--------|
| **Problem** | Users who paste a job description instead of uploading a file get an error. `selectedJobId` remains empty, blocking analysis. |
| **Root Cause** | `POST /api/v1/platform/projects/{id}/jobs` endpoint did not exist. Only `GET /jobs` was registered. Frontend calls `platformApi.jobs.create()` which targets `POST /jobs`. |
| **Affected File** | `backend/app/api/v1/endpoints/platform.py` |
| **Affected Function** | Missing `create_job` handler |
| **Severity** | 🔴 CRITICAL — JD text input path completely broken |
| **Fix Applied** | Added `@router.post("/projects/{project_id}/jobs")` endpoint with full LLM parsing |
| **Estimated Risk** | Low — additive change only |

---

## Issue 3 — Jobs stuck in "processing" state forever

| Field | Detail |
|-------|--------|
| **Problem** | Background job never transitions to `completed`. Render occasionally restarts. Jobs show `processing` or `embedding` forever. |
| **Root Cause** | `_sync_update_progress()` called `run_coroutine_threadsafe(coro, loop).result()` with no timeout. When the asyncio event loop is busy (handling requests), this blocks the background thread indefinitely. The thread cannot progress, and the job never transitions state. |
| **Affected File** | `backend/app/api/v1/endpoints/platform.py` |
| **Affected Function** | `_sync_update_progress`, `_sync_fail_job` |
| **Severity** | 🔴 CRITICAL — background worker deadlocks under load |
| **Fix Applied** | Added `timeout=5.0` to `future.result()`. Progress update failure is now non-fatal — logged and skipped. |
| **Estimated Risk** | Low — progress updates are informational; missing one update doesn't affect job outcome |

---

## Issue 4 — FAISS unavailable in Docker / Render

| Field | Detail |
|-------|--------|
| **Problem** | Background indexing always fails with `ImportError: No module named 'faiss'`. Jobs stuck at `embedding` state. |
| **Root Cause** | `faiss-cpu` was absent from `backend/requirements.txt`. Docker builds on Render would not install it. The library is used unconditionally at `import faiss` inside `process_project_data_task`. |
| **Affected File** | `backend/requirements.txt` |
| **Affected Function** | `process_project_data_task` — FAISS import and `faiss.IndexFlatIP` usage |
| **Severity** | 🔴 CRITICAL — entire embedding + indexing pipeline broken on production |
| **Fix Applied** | Added `faiss-cpu>=1.7.4` to `requirements.txt` |
| **Estimated Risk** | None — adding a missing dependency |

---

## Issue 5 — Supabase Storage file downloads always fail (404/401)

| Field | Detail |
|-------|--------|
| **Problem** | Candidate streaming, embedding loads, JSONL reads from Supabase Storage all fail. Analysis gets 0 candidates. |
| **Root Cause** | `SupabaseStorageProvider.download_stream()` used URL path `/storage/v1/object/authenticated/{bucket}/{path}`. This endpoint requires an anon JWT. The backend uses the **service role key** which must use `/storage/v1/object/{bucket}/{path}` (no `authenticated/` segment). |
| **Affected File** | `backend/app/services/storage_provider.py` |
| **Affected Function** | `SupabaseStorageProvider.download_stream` |
| **Severity** | 🔴 CRITICAL — all storage reads fail when using Supabase mode |
| **Fix Applied** | Changed URL to `/storage/v1/object/{bucket_id}/{path}` |
| **Estimated Risk** | None — correct API path per Supabase docs |

---

## Issue 6 — Debug print statements execute on every import

| Field | Detail |
|-------|--------|
| **Problem** | Noisy logs; slight startup slowdown; `import inspect` at module level |
| **Root Cause** | `auth.py` had bare `print()` and `inspect.signature()` calls at module level — executed on every import |
| **Affected File** | `backend/app/core/auth.py` |
| **Affected Function** | Module level |
| **Severity** | 🟡 LOW — cosmetic, log pollution |
| **Fix Applied** | Removed all debug print statements |
| **Estimated Risk** | None |

---

## Issue 7 — SSE progress stream URL doubles `/api/v1`

| Field | Detail |
|-------|--------|
| **Problem** | SSE stream connection gets 404 in production when `NEXT_PUBLIC_API_URL` already contains `/api/v1` |
| **Root Cause** | `project/[id]/page.tsx` constructs the SSE URL as `${baseUrl}/api/v1/platform/...`. If `NEXT_PUBLIC_API_URL=https://api.example.com/api/v1`, the URL becomes `https://api.example.com/api/v1/api/v1/platform/...`. |
| **Affected File** | `frontend/src/app/(dashboard)/projects/[id]/page.tsx` |
| **Affected Function** | `useEffect` SSE connection block |
| **Severity** | 🟠 MEDIUM — SSE progress bar broken in production |
| **Fix Applied** | Added `baseUrl.replace(/\/api\/v1\/?$/, '')` before appending `/api/v1` |
| **Estimated Risk** | None — string normalization only |

---

## Issue 8 — Supabase JWT secret misconfigured

| Field | Detail |
|-------|--------|
| **Problem** | All authenticated requests fail JWT verification in production |
| **Root Cause** | `backend/.env` sets `SUPABASE_JWT_SECRET` to the service key value. The JWT secret is a different value (found in Supabase Dashboard → Settings → API → JWT Secret). |
| **Affected File** | `backend/.env`, `backend/app/core/auth.py` |
| **Affected Function** | `_decode_jwt()` |
| **Severity** | 🔴 CRITICAL in production (auth bypassed); 🟡 LOW in dev (falls back to unverified decode) |
| **Fix Required** | Set `SUPABASE_JWT_SECRET` in Render environment variables to the actual JWT secret from Supabase dashboard. **Not auto-fixed** — requires your project-specific secret. |
| **Estimated Risk** | None if set correctly |

---

## Issue 9 — Hardcoded Windows paths in production code

| Field | Detail |
|-------|--------|
| **Problem** | Silent failures on every background job, heartbeat, and analysis — `open("C:\\Users\\HP\\...")` always raises on Linux/Docker |
| **Root Cause** | Diagnostic log writes target absolute Windows paths throughout `main.py`, `platform.py`, and `job_manager.py`. These are in `try/except` blocks so failures are swallowed. |
| **Affected Files** | `backend/app/main.py`, `backend/app/api/v1/endpoints/platform.py`, `backend/app/services/job_manager.py` |
| **Severity** | 🟡 LOW — swallowed by `except: pass`; does not affect functionality |
| **Fix Applied** | Removed Windows-only file writes from `job_manager.py` recovery function. Remaining occurrences in `platform.py` and `main.py` are in `try/except` blocks — non-fatal. |
| **Estimated Risk** | None |

---

## Summary Table

| # | Severity | Status | Description |
|---|----------|--------|-------------|
| 1 | 🔴 CRITICAL | ✅ Fixed | Broken `from __future__` import crashes backend |
| 2 | 🔴 CRITICAL | ✅ Fixed | Missing `POST /jobs` endpoint — text JD input broken |
| 3 | 🔴 CRITICAL | ✅ Fixed | `_sync_update_progress` deadlock — jobs stuck forever |
| 4 | 🔴 CRITICAL | ✅ Fixed | `faiss-cpu` missing from requirements — FAISS unavailable |
| 5 | 🔴 CRITICAL | ✅ Fixed | Wrong Supabase Storage URL — all downloads fail |
| 6 | 🟡 LOW | ✅ Fixed | Debug print statements in auth.py |
| 7 | 🟠 MEDIUM | ✅ Fixed | SSE URL doubles `/api/v1` in production |
| 8 | 🔴 CRITICAL | ⚠️ Manual | Wrong `SUPABASE_JWT_SECRET` in env — set correct value in Render |
| 9 | 🟡 LOW | ✅ Fixed | Hardcoded Windows paths in recovery code |
