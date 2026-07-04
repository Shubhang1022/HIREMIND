# BackgroundWorkerAudit.md — Background Worker Audit

## Worker Entry Point

**Function**: `process_project_data_task(project_id: str)`  
**File**: `backend/app/api/v1/endpoints/platform.py`  
**Trigger**: `BackgroundTasks.add_task(process_project_data_task, project_id)` — runs in a thread pool thread managed by Starlette's `BackgroundTasks`  
**Thread context**: Non-async (sync function), runs in `asyncio.to_thread` equivalent

---

## State Machine

```
queued
  ↓
processing       ← "Starting Indexing" + "Streaming Candidates" stages
  ↓
embedding        ← "Generating Embeddings" stage (batched, progress 20–80%)
  ↓
indexing         ← "Building FAISS Index" + "Uploading Indexes" stages
  ↓
completed        ← artifact validation passed, project.embedding_status = "completed"

From any non-terminal state:
  → failed       ← exception after max retries, or watchdog timeout
  → cancelled    ← cancellation token set via /cancel-indexing
  → retrying     ← after attempt N < 3 fails (2^N second backoff)
```

**FSM enforcement**: `JobManager.validate_transition(current, target)` via `VALID_TRANSITIONS` dict  
**Illegal transitions are rejected** and logged; they do NOT crash the worker

---

## Retry Logic

- Max retries: 3 attempts
- Backoff: `2^attempt` seconds (2s, 4s, 8s)
- On final failure: `_sync_fail_job()` + `projects.embedding_status = "failed"`
- Recovery at startup: `JobManager.recover_interrupted_jobs()` re-queues jobs with `retry_count < 3`

---

## Heartbeat

- `log_worker_heartbeat(stage, processed, total, batch_num)` called every 5 seconds during candidate streaming
- Updates `background_jobs.last_heartbeat` via `_sync_update_progress()`
- In-memory `_progress_cache` updated on every progress call
- Watchdog in `_run_worker_watchdog()`: marks jobs stuck for >10 minutes as `failed`

---

## Cancellation

- `JobManager.request_cancellation(project_id)` adds project_id to `_cancellation_tokens`
- Worker checks `job_manager.is_cancelled(project_id)` every 10 candidates and before each major stage
- On cancellation: status set to `cancelled`, cancellation token cleared, worker returns cleanly
- API: `POST /projects/{id}/cancel-indexing`

---

## Progress Updates (`_sync_update_progress`)

**Issue found (pre-fix)**: Called with `run_coroutine_threadsafe(coro, loop).result()` which **blocks the calling thread indefinitely** if the event loop is saturated.

**Fix applied**: Added `timeout=5.0` to `future.result()`. Progress update failure is now non-fatal — it logs and continues rather than blocking the entire background worker.

---

## Memory Management

- Working files written to `tempfile.mkdtemp()` directory
- Raw embeddings streamed to disk (`.raw` file) — not held in RAM
- `.npy` assembled from raw file in chunks
- `finally` block: `shutil.rmtree(temp_dir)` + `gc.collect()`
- Role files: opened as file handles during streaming, closed after streaming phase
- `CacheService.invalidate_project(project_id)` called in `finally`

---

## Embedding Worker

| Step | Detail |
|------|--------|
| Model | `EmbeddingEncoder` from `src/features/embedding.py`, model `BAAI/bge-large-en-v1.5` |
| Loading | Lazy-loaded once via `_get_encoder()`, cached in module-level `_encoder` |
| Batch size | 32 candidates |
| Disqualified candidates | Encoded as zero-vector (not sent to model) |
| Encoding failure | Retries once on `encode_batch` exception |
| FAISS | `faiss.IndexFlatIP(dim)` — inner product; embeddings added per batch |

---

## Artifact Integrity Verification

After all uploads complete, validates existence of:
- `embeddings/{project_id}/embeddings_v{N}.npy`
- `faiss-indexes/{project_id}/faiss_v{N}.index`
- `embeddings/{project_id}/ids_v{N}.json`
- `skill-indexes/{project_id}/skill_index_v{N}.json`
- `role-indexes/{project_id}/role_{CAT}_v{N}.jsonl` (for each category found)

If any are missing → `FileNotFoundError` raised → retry loop triggers

---

## Known Fixed Issues

| # | Issue | Fix |
|---|-------|-----|
| 1 | `_sync_update_progress` blocked background thread indefinitely | Added `timeout=5.0` to `future.result()`, made failure non-fatal |
| 2 | `faiss-cpu` missing from `requirements.txt` | Added `faiss-cpu>=1.7.4` |
| 3 | Broken import (`from sqlalchemy.connectors import asyncio` + misplaced `from __future__`) | Fixed import order — `from __future__` now first line |
| 4 | Hardcoded Windows paths for heartbeat logs | Removed `open(recovery_path...)` Windows-only writes; logging only via `logger` |
