# RenderStabilityReport.md — Render Platform Stability Audit

## Memory Profile

| Condition | RAM Usage | Risk |
|-----------|-----------|------|
| Idle (no active jobs) | ~150–200 MB | Low |
| During embedding (BAAI/bge-large-en-v1.5 loaded) | ~800–1200 MB | High on free tier |
| During FAISS index build (10k candidates) | ~200–300 MB additional | Medium |
| During analysis (FAISS loaded + LLM call) | ~400–600 MB | Medium-High |
| Render free tier RAM limit | 512 MB | Exceeded during embedding |

**Recommendation**: Render Standard instance (2 GB RAM) required. Free tier (512 MB) will OOM during embedding model loading.

---

## OOM Risk Points

### 1. SentenceTransformer model load (~800 MB)
- Triggered once on first upload
- Model stays in `_encoder` module-level cache
- Not released until process restart
- **Mitigation**: The 450MB RAM threshold fallback in `run_analysis()` detects this and returns metadata-only results

### 2. FAISS index in memory during analysis
- 10k candidates × 1024 dim × 4 bytes = ~40 MB
- Acceptable on Standard tier

### 3. Entire JSONL file re-read for pagination
- `list_candidates()` streams entire file for each page request
- For 50k candidates, each page request reads ~50k records
- **Risk**: Not OOM but very slow; can cause request timeouts on Render (30s default)

---

## Thread Safety

### Worker threads
- `BackgroundTasks.add_task()` runs in Starlette's thread pool (not the event loop)
- `_active_analyses` is a `set` accessed from both async (analysis endpoint) and sync (cleanup) contexts — **not thread-safe** but acceptable for single-worker Render deployment

### Singleton pattern
- `JobManager` uses `__new__` singleton — thread-safe only for single-process deployments
- Render runs single worker by default (`uvicorn --workers 1`) → safe
- If scaled to multiple workers, the in-memory `_progress_cache` and `_cancellation_tokens` will NOT be shared → each worker has independent state

---

## Startup Sequence

1. `verify_ai_dependencies()` — checks `faiss`, `numpy`, `torch`, `transformers`, `sentence_transformers`
2. `log_deployment_diagnostics("STARTUP")` — logs RAM, CPU, thread count
3. `run_startup_initialization()`:
   - `_verify_background_jobs_table_exists()`
   - `recover_interrupted_jobs()` — re-queues jobs interrupted by previous restart
   - `_enforce_analysis_timeouts()` — fails projects stuck for >30 min
   - `_enforce_embedding_timeouts()` — fails projects stuck for >1 hour
4. CORS origins validated — raises `ValueError` if wildcard mixed with `allow_credentials=True`

**Risk**: If `recover_interrupted_jobs()` re-queues many jobs simultaneously at startup, it can spike RAM by loading multiple embedding models. Currently limited by `asyncio.create_task()` — tasks are non-blocking.

---

## Shutdown Sequence

1. `cancel_all_active_jobs()` — marks in-progress jobs as cancelled in DB
2. `CacheService.clear()` — releases in-memory caches
3. `gc.collect()` — explicit garbage collection
4. 30-second timeout enforced via `asyncio.wait_for()`
5. Render sends SIGTERM → lifespan `yield` exit → shutdown handler runs

---

## Blocking Code Risks

| Location | Issue | Risk |
|----------|-------|------|
| `process_project_data_task` | Entire function is synchronous (no `await`) | Acceptable — runs in thread pool |
| `_sync_update_progress` (pre-fix) | `.result()` with no timeout could block thread | **FIXED**: timeout=5s added |
| `list_candidates()` | Full JSONL stream per request — blocking I/O in async handler | Medium — no thread offload |
| `compute_dataset_hash()` | Streams entire candidate file synchronously in async route | Low for small datasets |

---

## Infinite Loop Risks

| Location | Risk |
|----------|------|
| `event_generator()` in SSE stream | Loops until status is terminal; if job is stuck in non-terminal state, SSE streams forever. Mitigated by watchdog that eventually marks stuck jobs as `failed` |
| `recover_interrupted_jobs()` → `_safely_run_indexing()` | If recovery re-triggers a job that immediately fails and is re-queued at the next restart, this creates a restart loop. Mitigated by `retry_count` limit (max 3) |

---

## CORS After Backend Crash

When Render restarts the backend container, in-flight requests from the frontend get a TCP reset or empty response. Since the CORS headers are added by the backend middleware, a missing response looks like a CORS failure to the browser's `fetch()` API.

**Root cause**: Not actually a CORS bug — it's the backend crashing. The primary crash was the `from __future__` import bug (now fixed). Fixing the startup crash eliminates these false CORS errors.

---

## Recommendations

1. Set Render instance to **Standard (2 GB RAM)** — required for embedding model
2. Configure `SUPABASE_JWT_SECRET` to the actual JWT secret from Supabase dashboard Settings → API
3. Set `USE_SUPABASE_STORAGE=true` in production (storage_provider fix already applied)
4. Set `OPENROUTER_API_KEY` with active credits for LLM scoring
5. Set `CORS_ORIGINS=https://your-frontend.vercel.app` in Render environment
