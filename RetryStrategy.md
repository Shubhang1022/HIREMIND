# RetryStrategy.md

**Generated:** 2026-07-09

---

## Automatic Retry (backend-internal)

### Within `process_project_data_task`

The task has an internal retry loop: `for attempt in range(1, max_retries + 1)` where `max_retries = 3`.

Backoff between attempts: `2^attempt` seconds (2s, 4s, 8s).

**Retried automatically (transient):**
- Storage upload/download failures
- Temporary network interruptions
- `encode_batch` failures (retried once inline)
- Any unexpected exception

**Not retried (non-transient — immediate permanent fail):**
- `ModelLoadTimeout` / `ModelLoadFailed` → marked `MODEL_LOAD_FAILED`; process exits retry loop immediately
- `INDEX_DIMENSION_MISMATCH` → model changed between indexing runs; requires re-upload

---

### At Server Startup — `recover_interrupted_jobs`

Jobs stuck in `{queued, processing, embedding, indexing, retrying}` are recovered on boot.

| Retry count | Backoff delay |
|---|---|
| 1st retry | 60 seconds |
| 2nd retry | 120 seconds |
| 3rd retry | 300 seconds |
| 4th+ | Permanently failed |

**Permanent failure conditions:**
- `retry_count >= 3`
- `failure_reason` contains `MODEL_LOAD_FAILED`, `MODEL_LOAD_TIMEOUT`, or `INDEX_DIMENSION_MISMATCH`

---

### Phase 5 — Auto-Resume on Boot

If a project has `embedding_status='failed'` but `current_candidate_path` exists (file is stored), the backend auto-resumes indexing with a 15-second delay after boot — **without re-uploading**.

---

## Manual Retry (user-triggered)

### Endpoint: `POST /api/v1/platform/projects/{id}/retry-indexing`

Behaviour:
1. Requires `embedding_status = 'failed'`
2. Returns 409 if already indexing or completed
3. Returns 400 if no candidate file exists
4. Resets `embedding_status = 'queued'`
5. Registers a new `background_jobs` row
6. Kicks off `process_project_data_task` — **reuses the stored file, no re-upload**

Response:
```json
{
  "status": "queued",
  "message": "Indexing restarted using existing candidate file. No re-upload needed.",
  "job_id": "...",
  "project_id": "..."
}
```

---

## HTTP 409 Recovery Flow (Phase 4)

**Before fix:**
```
409 "Candidate indexing failed. Please re-upload candidate files to retry."
```

**After fix:**
```json
{
  "code": "INDEXING_FAILED",
  "message": "Candidate indexing failed. Use the retry endpoint to restart indexing — no re-upload required.",
  "retry_endpoint": "/api/v1/platform/projects/{id}/retry-indexing",
  "action": "retry_indexing"
}
```

The frontend now shows a **"Retry Indexing — No Re-upload Needed"** button whenever `embedding_status === 'failed'`, in both the Candidates tab and the Results tab.

---

## Decision Tree

```
Indexing failed?
├── Is failure_reason MODEL_LOAD_* or INDEX_DIMENSION_MISMATCH?
│   └── YES → Cannot auto-retry. Show diagnostic. User must check model/re-upload.
└── NO (transient failure)
    ├── retry_count < 3?
    │   └── YES → Auto-retry with backoff (startup recovery OR internal loop)
    └── retry_count >= 3?
        └── YES → Mark permanent failure. Show "Retry Indexing" button.
                  User clicks → POST /retry-indexing → fresh job, same file.
```
