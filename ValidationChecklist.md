# ValidationChecklist.md — Final Production Validation

## Code Validation (run locally)

### Architecture invariants

| Check | How to verify | Expected result |
|-------|--------------|-----------------|
| `SentenceTransformer()` only in `model_service.py` | `grep -r "SentenceTransformer(" backend/` | Only `backend/app/services/model_service.py` |
| No `load_model()` call in pipeline | `grep -r "\.load_model()" backend/` | Zero results |
| No `EmbeddingEncoder()` in endpoints | `grep -r "EmbeddingEncoder(" backend/app/api/` | Zero results |
| `preload_model_singleton` called at startup | `grep -n "preload_model_singleton" backend/app/main.py` | Found in lifespan |
| Recovery has backoff delay | `grep -n "backoff" backend/app/services/job_manager.py` | Found in `recover_interrupted_jobs` |
| Watchdog uses 2-min threshold | `grep -n "WATCHDOG_TIMEOUT" backend/app/api/v1/endpoints/platform.py` | Default `"2"` |
| `[RECOVERY_SUMMARY]` logged | `grep -n "RECOVERY_SUMMARY" backend/app/services/job_manager.py` | Found |
| `[STARTUP_SUMMARY]` logged | `grep -n "STARTUP_SUMMARY" backend/app/main.py` | Found |
| Health endpoint returns model.loaded | `grep -n "model_loaded" backend/app/main.py` | Found in `/health` handler |

### Diagnostics check (all pass ✅)

```
platform.py  : No diagnostics found
main.py      : No diagnostics found
job_manager.py : No diagnostics found
model_service.py : No diagnostics found
```

---

## Startup Log Validation

After starting the backend locally (`cd backend && uvicorn app.main:app --reload`):

### Expected within 0–5 seconds

```
[STARTUP_SUMMARY] pid=... rss=...MB avail_ram=...MB model=BAAI/bge-base-en-v1.5 env=development missing_vars=...
[MODEL_SERVICE] Starting background preload for model=BAAI/bge-base-en-v1.5
--- Verifying AI Dependencies ---
✓ FAISS Loaded
✓ Transformers Verified (Installed)
✓ SentenceTransformer Verified (Installed)
[RECOVERY_SUMMARY] recovered=0 skipped=0 permanent_failures=0 retry_counts=none
```

### Expected within 40–120 seconds (model download/load)

```
[MODEL_SERVICE] [MODEL_CACHE_MISS] name=BAAI/bge-base-en-v1.5
[MODEL_SERVICE] [MODEL_LOAD_COMPLETE] name=BAAI/bge-base-en-v1.5 elapsed=Xs
[MODEL_SERVICE] [MODEL_SINGLETON_CREATED] name=BAAI/bge-base-en-v1.5
```

If model already cached (subsequent starts):

```
[MODEL_SERVICE] [MODEL_CACHE_HIT] name=BAAI/bge-base-en-v1.5
```

---

## Pipeline Validation (50-candidate dataset)

Upload a 50-candidate JSONL file and verify these log lines appear in order:

```
[BACKGROUND_TASK_START] Project ID: ... Memory: ...MB
[STAGE_START] stage=upload_indexes
[STAGE_END]   stage=upload_indexes elapsed=...s
[STAGE_START] stage=load_model model=BAAI/bge-base-en-v1.5
[MODEL_SERVICE] [MODEL_CACHE_HIT] model already loaded — skipping download   ← key line
[STAGE_END]   stage=load_model elapsed=0.00s   ← instant, no download
[STAGE_START] stage=generate_embeddings total_candidates=50 total_batches=2
[EMBEDDING_BATCH] project=... batch=1/2 processed=32/50 progress=58% speed=... elapsed=...
[EMBEDDING_BATCH] project=... batch=2/2 processed=50/50 progress=78% speed=... elapsed=...
[STAGE_END]   stage=generate_embeddings elapsed=...s processed=50 batches=2 dim=768
[STAGE_START] stage=write_npy
[STAGE_END]   stage=write_npy elapsed=...s
[STAGE_START] stage=build_faiss ntotal=50
[STAGE_END]   stage=build_faiss elapsed=...s ntotal=50
[STAGE_START] stage=upload_artifacts
[STAGE_END]   stage=upload_artifacts elapsed=...s
[STAGE_START] stage=validate_artifacts
[STAGE_END]   stage=validate_artifacts all_present=True
[STAGE_START] stage=mark_completed
[STAGE_END]   stage=mark_completed elapsed=...s
[BACKGROUND_TASK_SUCCESS] Project ID: ... Elapsed: ...s
```

---

## SSE Validation

During indexing, the SSE stream (`/api/v1/platform/projects/{id}/progress-stream`) must:

| Check | Expected |
|-------|---------|
| Connects without 502 | ✅ |
| Progress starts at 5%, not stuck at 20% | ✅ |
| Progress advances per batch | 25% → 28% → 36% → ... → 78% → 85% → 100% |
| `current_stage` shows batch info | `"Embedding batch 1/2 (32/50)"` |
| Heartbeat pings sent every 5s | `: heartbeat` lines visible in raw SSE |
| Terminal event sent when complete | `{"status":"completed","progress_percentage":100}` |
| Frontend loads project after complete | `embedding_status = "completed"`, "Run AI Analysis" enabled |

---

## Health Endpoint Validation

```bash
curl http://localhost:8000/health
```

Expected response shape:

```json
{
  "status": "healthy",
  "timestamp": 1234567890.0,
  "uptime_seconds": 142.3,
  "database": { "status": "healthy", "error": null },
  "storage": { "status": "healthy", "error": null },
  "openrouter": { "status": "configured" },
  "model": {
    "loaded": true,
    "name": "BAAI/bge-base-en-v1.5",
    "load_state": "loaded",
    "configured_model": "BAAI/bge-base-en-v1.5"
  },
  "faiss": { "available": true },
  "memory": {
    "rss_mb": 450.2,
    "available_mb": 62.8,
    "safety_limit_mb": 450.0,
    "under_threshold": false
  },
  "cpu_percent": 1.2,
  "threads": 12,
  "background_jobs": {
    "active": [],
    "active_count": 0,
    "failed_total": 0
  },
  "ranking_cache_size": 0
}
```

---

## Watchdog Validation

1. Start an indexing job.
2. Kill the background thread mid-run (e.g. manually raise an exception or stop uvicorn).
3. Wait 2 minutes.
4. Call `GET /api/v1/platform/projects/{id}/worker-status`.
5. Expected: `"status": "failed"`, `"failure_reason": "Watchdog timeout: no heartbeat for 2.x minutes"`.

---

## Environment Variable Validation

With a missing variable (e.g. unset `SUPABASE_URL`), startup logs must show:

```
[STARTUP_ERROR] Required environment variable SUPABASE_URL is not set.
                Description: Supabase project URL
```

And the startup summary must include:

```
Env Vars     : ✗ MISSING VARS: SUPABASE_URL
```

The process must still start and serve `/health` — it should not crash.

---

## Final Sign-Off Checklist

- [ ] `SentenceTransformer()` confirmed only in `model_service.py`
- [ ] `load_model()` confirmed absent from backend pipeline
- [ ] All 4 backend files pass diagnostics (no errors)
- [ ] `[STARTUP_SUMMARY]` appears in startup logs
- [ ] `[RECOVERY_SUMMARY]` appears in startup logs  
- [ ] `/health` returns `model.loaded=true` after warm-up
- [ ] Progress advances continuously batch-by-batch
- [ ] `[EMBEDDING_BATCH]` log emitted per batch
- [ ] `[EMBEDDING_MONITOR]` log emitted every 10s
- [ ] SSE sends terminal event on completion
- [ ] Frontend shows "Run AI Analysis" after completion
- [ ] Watchdog marks stale jobs failed within 2 minutes
- [ ] Recovery does not retry `MODEL_LOAD_FAILED` jobs
- [ ] `DeploymentChecklist.md` reviewed before pushing to Render
