# RootCauseAnalysis.md

## Primary Blocker: `TypeError: _sync_update_progress() got an unexpected keyword argument 'processed_candidates'`

### Exact crash location

```
backend/app/api/v1/endpoints/platform.py
process_project_data_task()  →  line ~1316

_sync_update_progress(project_id, "Loading Embedding Model", 20, status="embedding",
                      processed_candidates=0, total_candidates=total_candidates,
                      retry_count=attempt - 1)
```

### Root cause: parameter name mismatch between the definition and its callers

**Definition of `_sync_update_progress`** (line 1766, before fix):
```python
def _sync_update_progress(
    project_id, stage, progress,
    status=None,
    processed=0,         # ← old name
    total=0,             # ← old name
    eta="",
    retry_count=None
):
```

**Callers added in recent edits** (throughout embedding stage):
```python
_sync_update_progress(project_id, stage_label, progress_pct,
                      status="embedding",
                      processed_candidates=global_idx,    # ← new name
                      total_candidates=total_candidates,  # ← new name
                      retry_count=attempt - 1)
```

The callers use `processed_candidates=` and `total_candidates=` — the same names that `update_job_progress` (the async coroutine in `job_manager.py`) accepts. But `_sync_update_progress` (the sync wrapper) only accepted `processed=` and `total=`. Python raises `TypeError: got an unexpected keyword argument 'processed_candidates'` before even calling the coroutine.

### Timeline of divergence

1. `job_manager.update_job_progress` was always defined with `processed_candidates` and `total_candidates` (correct)
2. `_sync_update_progress` was originally written with short names `processed` and `total` and passed them positionally to `update_job_progress`
3. Multiple rounds of edits added new `_sync_update_progress` call sites using the keyword forms `processed_candidates=` and `total_candidates=` — matching the coroutine's parameter names
4. The wrapper was never updated → TypeError on every call that uses the new keyword style

### Secondary issue: FSM illegal transition `embedding → processing`

On retry attempt 2+, the job cache may already be in `"embedding"` state. The old code always called:
```python
_sync_update_progress(project_id, "Starting Indexing", 5, status="processing", ...)
```
This tried to transition `embedding → processing` — rejected by the FSM's `validate_transition`. The fix:
```python
_target_status = "processing" if _current_job_status in ("queued", "retrying") else _current_job_status
```
On retry from `embedding` state, the status stays `embedding` (idempotent, valid).

---

## Fix Applied

`_sync_update_progress` was rewritten to:
1. Accept both the old-style `processed` / `total` AND the new-style `processed_candidates` / `total_candidates`
2. New-style kwargs take precedence when both are provided
3. Accept `**_ignored_kwargs` to silently absorb any future fields — callers can add new parameters without ever causing another TypeError here
4. All call sites continue working unchanged
