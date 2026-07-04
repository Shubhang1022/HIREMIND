# RecoveryFlow.md — Job Recovery Flow

## Before This Fix (infinite loop)

```
Render restarts process
  └─ recover_interrupted_jobs()
       └─ finds job: status=embedding, retry_count=0
       └─ retry_count < 3 → new_retry=1
       └─ DB update: status=retrying, retry_count=1
       └─ asyncio.create_task(_safely_run_indexing(project_id))
            └─ process_project_data_task()
                 └─ _get_encoder()
                      └─ SentenceTransformer(bge-large, ...)  ← HANGS
                           ← Render kills process
                           ← RESTART
                           └─ recover_interrupted_jobs()
                                └─ status=retrying, retry_count=1
                                └─ retry_count < 3 → new_retry=2
                                └─ DB update: retry_count=2
                                └─ _safely_run_indexing()  ← HANGS AGAIN
                                     ← RESTART
                                     └─ retry_count < 3 → new_retry=3 ← HANGS AGAIN
                                          ← RESTART
                                          └─ retry_count >= 3 → PERMANENTLY FAILED
                                             (after 3 full restart cycles, each ~5+ min)

Total time stuck: 15–30 minutes minimum
Frontend: frozen at 20% for entire duration
SSE: returning 502 on every restart
```

---

## After This Fix (exponential backoff + non-retryable tagging)

```
Render restarts process
  └─ preload_model_singleton()       ← daemon thread starts model download
  └─ recover_interrupted_jobs()
       └─ finds job: status=embedding, retry_count=0, failure_reason=<empty>
       └─ is_non_retryable? NO
       └─ retry_count < 3 → new_retry=1
       └─ backoff_delay = BACKOFF_SECONDS[1] = 60s
       └─ DB update: status=retrying, current_stage="Recovering (attempt 1/3, backoff 60s)"
       └─ asyncio.create_task(_safely_run_indexing_with_backoff(project_id, 60))
            └─ await asyncio.sleep(60)    ← non-blocking, event loop free
            └─ process_project_data_task()
                 └─ _get_encoder()
                      └─ model_service.get_model()
                           └─ is_loaded()? YES (preload completed in 60s) → returns instantly
                           └─ [MODEL_CACHE_HIT]
                 └─ encode_batch()  ← WORKS
                 └─ pipeline completes → status=completed
```

### If model load fails during recovery:

```
process_project_data_task()
  └─ _get_encoder()
       └─ model_service.get_model()
            └─ times out (120s) → raises ModelLoadTimeout
  └─ stage_exc = ModelLoadTimeout(...)
  └─ isinstance(stage_exc, ModelLoadTimeout) == True
  └─ _sync_fail_job(project_id, "MODEL_LOAD_FAILED: ...")
  └─ DB update: failure_reason = "MODEL_LOAD_FAILED: ..."
  └─ in-memory cache: status = "failed"
  └─ returns immediately (no retry)

Next restart:
  └─ recover_interrupted_jobs()
       └─ job.failure_reason contains "MODEL_LOAD_FAILED"
       └─ is_non_retryable == True
       └─ PERMANENTLY FAILED — no further restarts
```

---

## Backoff Schedule

| Attempt | Delay | Total elapsed before retry |
|---------|-------|--------------------------|
| 1 | 60s | 60s |
| 2 | 120s | 180s (3 min) |
| 3 | 300s | 480s (8 min) |
| >3 | permanent fail | — |

The 60s delay on attempt 1 is intentionally set to give the preload daemon thread time to
finish downloading `bge-base-en-v1.5` (~40–80s on Render's network).

---

## Non-Retryable Failure Reasons

The following `failure_reason` strings cause `recover_interrupted_jobs()` to permanently
fail the job without scheduling a retry:

- `MODEL_LOAD_FAILED`
- `MODEL_LOAD_TIMEOUT`
- `model_load_failed`

This prevents the "retry → hang → restart → retry" loop that was occurring before.

---

## State Diagram

```
queued
  │
  ▼
processing ──────────────────────────────────────────┐
  │                                                   │
  ▼                                                   │
embedding (model load hangs)                          │
  │                                                   │
  │ [Render restarts]                                 │
  ▼                                                   │
retrying (backoff 60s / 120s / 300s)                 │
  │                                                   │
  ├── model now loaded → processing → ... → completed │
  │                                                   │
  └── model still fails → failed (non-retryable) ◄───┘
                            │
                            ▼
                        [PERMANENT — no further recovery]
```
