# ProgressSynchronization.md — Progress State Synchronization Audit

## Progress Flow (Post-Fix)

Progress values are now guaranteed to advance on every stage transition. The pipeline can never remain permanently at 20%.

| Stage | Progress % | Status field | DB update |
|-------|-----------|-------------|-----------|
| Task start | 5% | `processing` | ✓ `background_jobs` |
| Streaming candidates | 10% | `processing` | ✓ |
| Per-candidate stream heartbeat | 10% (unchanged) | `processing` | ✓ every 5s |
| After streaming — before model load | 20% | `embedding` | ✓ |
| Model loaded | 25% | `embedding` | ✓ |
| Each embedding batch | 25%–80% | `embedding` | ✓ per batch |
| Final partial batch | 80% | `embedding` | ✓ |
| FAISS build + npy write start | 85% | `indexing` | ✓ |
| Upload artifacts | 90% | `indexing` | ✓ |
| Validate artifacts | 90% | `indexing` | ✓ (no separate update) |
| Mark completed | 100% | `completed` | ✓ both `background_jobs` + `projects` |
| On any failure (final retry) | preserved | `failed` | ✓ in-memory first, then DB |

**Key guarantee**: Progress only advances. It never goes backward. Every `_sync_update_progress` call is validated by the FSM in `JobManager.validate_transition()` before the DB write.

---

## Pre-Fix Problem: Stuck at 20%

```
upload_indexes done
→ progress=20, status="embedding"     ← written to cache + DB
→ encoder = _get_encoder()            ← hangs (model download, no timeout)
→ [silence for 60–300s]
→ exception caught by outer try/except
→ attempt < max_retries → sleep → retry
→ same hang on attempt 2
→ same hang on attempt 3
→ _sync_fail_job(project_id, reason)  ← schedules async coroutine
→ in-memory cache: status still "embedding"  ← NEVER UPDATED
→ SSE reads cache: status="embedding", progress=20%  ← forever
→ SSE break condition never fires
→ frontend EventSource.onerror fires eventually
→ eventSource.close() — NO reconnect
→ UI frozen at 20%
```

---

## Post-Fix Flow: 20% → model load → 25% → batches → 80% → 100%

```
upload_indexes done
→ [STAGE_START] stage=upload_indexes
→ [STAGE_END]   stage=upload_indexes
→ progress=20, status="embedding"          ← written
→ [STAGE_START] stage=load_model           ← logged BEFORE call
→ encoder = _get_encoder()
   ├─ success:  [STAGE_END] stage=load_model elapsed=Xs
   └─ failure:  [STAGE_FAIL] stage=load_model (full traceback) → raise
→ progress=25, status="embedding"          ← written after model loaded
→ [STAGE_START] stage=generate_embeddings
→ batch 1: encode → add to FAISS
   → progress = 25 + int(32/50 × 55) = 60%  ← written per batch
→ batch 2: encode → add to FAISS
   → progress = 80%                          ← written
→ [STAGE_END]   stage=generate_embeddings
→ progress=85, status="indexing"           ← written
→ [STAGE_START] stage=write_npy
→ [STAGE_END]   stage=write_npy
→ [STAGE_START] stage=build_faiss
→ [STAGE_END]   stage=build_faiss
→ progress=90, status="indexing"           ← written
→ [STAGE_START] stage=upload_artifacts
→ [STAGE_END]   stage=upload_artifacts
→ [STAGE_START] stage=validate_artifacts
→ [STAGE_END]   stage=validate_artifacts
→ [STAGE_START] stage=mark_completed
→ projects.embedding_status = "completed"  ← DB
→ progress=100, status="completed"         ← cache + DB
→ [STAGE_END]   stage=mark_completed
→ [BACKGROUND_TASK_SUCCESS]
→ SSE reads: status="completed" → sends event → breaks loop
→ frontend: load() → project.embedding_status="completed"
→ "Run AI Analysis" button becomes enabled
```

---

## Two Sources of Truth

Progress is stored in two places simultaneously:

| Source | Read by | Update frequency | Survives restart |
|--------|---------|-----------------|-----------------|
| `JobManager._progress_cache` (in-memory) | SSE stream, worker-status endpoint | Every batch, every stage | ❌ No |
| `background_jobs` table (Supabase) | DB fallback in SSE, worker-status, watchdog, recovery | Every stage | ✅ Yes |

**On process restart**: `_progress_cache` is empty. The SSE stream falls back to reading the DB. If a job was mid-flight when the process restarted, the DB shows the last persisted stage (e.g., `status=embedding, progress=20`). `recover_interrupted_jobs()` at startup re-queues the job and resets to `status=retrying`.

---

## FSM Enforcement

All status transitions are validated by `JobManager.validate_transition()`:

```python
VALID_TRANSITIONS = {
    "queued":     {"processing", "failed", "cancelled"},
    "processing": {"embedding", "failed", "cancelled"},
    "embedding":  {"indexing", "failed", "cancelled"},
    "indexing":   {"completed", "failed", "cancelled"},
    "failed":     {"retrying", "queued"},
    "retrying":   {"processing", "failed", "cancelled"},
    "completed":  set(),   # terminal
    "cancelled":  set(),   # terminal
}
```

An illegal transition is rejected with a log error and the DB write is skipped. This prevents race conditions where two concurrent workers (e.g., recovery + fresh upload) corrupt the status.

---

## `update_job_progress` DB Update Filter

The DB update uses a status filter to prevent stale updates:

```python
supabase_client.table("background_jobs")
    .update(db_updates)
    .eq("project_id", project_id)
    .eq("status", current_status)   # ← only update if still in expected state
    .execute()
```

If the watchdog has already marked a job as `failed`, a delayed in-flight progress update will not overwrite it back to `embedding`.
