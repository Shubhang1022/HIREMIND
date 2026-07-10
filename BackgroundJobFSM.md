# BackgroundJobFSM.md

**Generated:** 2026-07-09  
**File:** `backend/app/services/job_manager.py`

---

## State Machine Definition

```python
VALID_TRANSITIONS = {
    "queued":      {"processing", "failed", "cancelled"},
    "processing":  {"embedding",  "failed", "cancelled"},
    "embedding":   {"indexing",   "failed", "cancelled"},
    "indexing":    {"completed",  "failed", "cancelled"},
    "failed":      {"retrying",   "queued"},
    "retrying":    {"processing", "failed", "cancelled"},
    "completed":   set(),   # terminal
    "cancelled":   set()    # terminal
}
```

---

## State Diagram

```
                    ┌─────────────┐
     upload done ──▶│   QUEUED    │──────────────────────────────────┐
                    └──────┬──────┘                                   │
                           │                                          ▼
                    ┌──────▼──────┐                           ┌────────────┐
                    │  PROCESSING │──────────────────────────▶│  CANCELLED │
                    └──────┬──────┘                           └────────────┘
                           │                                          ▲
                    ┌──────▼──────┐                                   │
                    │  EMBEDDING  │───────────────────────────────────┤
                    └──────┬──────┘                                   │
                           │                                          │
                    ┌──────▼──────┐                                   │
                    │   INDEXING  │───────────────────────────────────┤
                    └──────┬──────┘                                   │
                           │                                          │
                    ┌──────▼──────┐                          ┌────────┴───────┐
                    │  COMPLETED  │                          │     FAILED     │
                    └─────────────┘                          └────────┬───────┘
                                                                      │
                                                             ┌────────▼───────┐
                                                             │    RETRYING    │
                                                             └────────┬───────┘
                                                                      │
                                                              back to PROCESSING
```

---

## Stage Mapping — `process_project_data_task`

| Stage | `status` in FSM | `progress` | `current_stage` label |
|---|---|---|---|
| Start | `processing` | 5% | Starting Indexing |
| Streaming candidates | `processing` | 10% | Streaming Candidates |
| Role/skill indexes uploaded | `processing` | 20% | Generating Embeddings |
| Model loaded | `embedding` | 20–25% | Loading Embedding Model |
| Embedding batches | `embedding` | 25–78% | Embedding batch N/M |
| FAISS build | `indexing` | 85% | Building FAISS Index |
| Uploading artifacts | `indexing` | 90% | Uploading Indexes |
| Done | `completed` | 100% | Completed |
| Any unhandled exception | `failed` | — | Failed: {error} |

---

## FSM Enforcement

Every status update goes through `JobManager.validate_transition()`:

- **Same-state**: always allowed (idempotent)
- **Forward**: allowed per `VALID_TRANSITIONS`
- **Backward**: rejected with `logger.warning`
- **Illegal**: rejected with `logger.error`, update is a no-op

---

## Non-Retryable Failure Reasons

Jobs with these keywords in `failure_reason` are **permanently failed** at startup recovery and never requeued:

- `MODEL_LOAD_FAILED`
- `MODEL_LOAD_TIMEOUT`
- `INDEX_DIMENSION_MISMATCH`

---

## Recovery at Startup

`recover_interrupted_jobs()` runs on every server boot:

1. Queries all jobs in `{queued, processing, embedding, indexing, retrying}`
2. `retry_count >= 3` → permanently fail
3. Non-retryable reason → permanently fail
4. Otherwise → increment retry, schedule with backoff: attempt 1→60s, 2→120s, 3→300s

---

## Phase 5: Auto-Resume for Failed Projects

`_resume_indexing_for_eligible_projects()` runs on boot after recovery:

- Finds projects with `embedding_status IN ('failed', 'pending')` AND `current_candidate_path IS NOT NULL`
- Skips projects that already have an active job (in-memory or DB)
- Schedules a 15-second delayed retry — no re-upload required
