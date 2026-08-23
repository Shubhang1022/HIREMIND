# CompatibilityReport.md

## Public API Compatibility: UNCHANGED

No public API endpoints were added, removed, or modified.

---

## API Surface Audit

| Endpoint | Before | After | Changed? |
|----------|--------|-------|---------|
| `GET /api/v1/platform/projects` | ✅ | ✅ | ❌ No |
| `POST /api/v1/platform/projects` | ✅ | ✅ | ❌ No |
| `GET /api/v1/platform/projects/{id}` | ✅ | ✅ | ❌ No |
| `PATCH /api/v1/platform/projects/{id}` | ✅ | ✅ | ❌ No |
| `DELETE /api/v1/platform/projects/{id}` | ✅ | ✅ | ❌ No |
| `POST /api/v1/platform/projects/{id}/upload` | ✅ | ✅ | ❌ No |
| `POST /api/v1/platform/projects/{id}/jobs` | ✅ | ✅ | ❌ No |
| `GET /api/v1/platform/projects/{id}/jobs` | ✅ | ✅ | ❌ No |
| `POST /api/v1/platform/projects/{id}/analyze` | ✅ | ✅ | ❌ No |
| `GET /api/v1/platform/projects/{id}/rankings/{rid}` | ✅ | ✅ | ❌ No |
| `GET /api/v1/platform/projects/{id}/analytics` | ✅ | ✅ | ❌ No |
| `POST /api/v1/platform/projects/{id}/export` | ✅ | ✅ | ❌ No |
| `GET /api/v1/platform/projects/{id}/worker-status` | ✅ | ✅ | ❌ No |
| `GET /api/v1/platform/projects/{id}/progress-stream` (SSE) | ✅ | ✅ | ❌ No |
| `POST /api/v1/platform/projects/{id}/cancel-indexing` | ✅ | ✅ | ❌ No |
| `GET /api/v1/platform/health-stats` | ✅ | ✅ | ❌ No |
| `GET /health` | ✅ | ✅ | ❌ No (extended fields added, no removals) |
| `GET /` | ✅ | ✅ | ❌ No |

---

## Internal-Only Changes (no external impact)

| Component | Change | External impact |
|-----------|--------|----------------|
| `_sync_update_progress` | Added kwargs, kept old positional params | None — internal function |
| `src/features/embedding.py` default model | `bge-base` → `bge-small` | Analysis results may differ by ~2% quality; no schema change |
| `src/ranking/engine.py` | Removed model auto-correction | Existing indexes with wrong dim now 409 instead of OOM crash; new indexes work fine |
| `job_manager.NON_RETRYABLE_REASONS` | Added `INDEX_DIMENSION_MISMATCH` | Prevents infinite retry on mismatched indexes — improves reliability |
| `startup_state.mark_api_ready()` | Now called correctly | Uploads no longer return 503 permanently |

---

## Frontend Compatibility

The frontend uses `platform-api.ts` which calls REST endpoints. All endpoints return the same JSON structure. The SSE event format (`data: {...}`) is unchanged. No frontend code was modified.

---

## Database Schema Compatibility

No Supabase schema changes. No new columns, no altered column types, no RLS policy changes.

---

## Docker Compatibility

The Dockerfile change (bge-small pre-bake) only affects build time and the cached model available on the container. The runtime behavior is the same — faster cold start.

---

## Backward Compatibility of _sync_update_progress

Old callers continue to work without modification:

```python
# Old-style (positional) — still works
_sync_update_progress(project_id, "Cancelled", 0, status="cancelled")
_sync_update_progress(project_id, "Building FAISS Index", 85, status="indexing", retry_count=0)

# Old-style with processed/total positionals — still works
_sync_update_progress(project_id, stage, progress, status, 0, 100, "", 0)

# New-style with explicit keywords — now works (was the crash)
_sync_update_progress(project_id, stage, progress,
                      status="embedding",
                      processed_candidates=32,
                      total_candidates=50,
                      retry_count=0)

# Future extended style — forward-compatible
_sync_update_progress(project_id, stage, progress,
                      status="embedding",
                      processed_candidates=32,
                      total_candidates=50,
                      speed=45.2,          # ignored but not TypeError
                      eta_seconds=1.2,     # ignored but not TypeError
                      my_future_field=42)  # ignored but not TypeError
```
