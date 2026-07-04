# SSEAudit.md — Server-Sent Events Audit

## Endpoint

`GET /api/v1/platform/projects/{project_id}/progress-stream`  
Handler: `get_progress_stream()` in `backend/app/api/v1/endpoints/platform.py`  
Media type: `text/event-stream`

---

## Pre-Fix Bugs

### Bug 1 — Terminal state never sent when worker fails silently

**Root cause**: In `process_project_data_task`, when an exception was caught on the final retry attempt, `_sync_fail_job()` was called — but the in-memory `_progress_cache[project_id]["status"]` was NOT updated to `"failed"` before calling it. The call to `_sync_fail_job` schedules an async coroutine on the event loop with a 5s timeout, but by the time it resolves, the SSE loop may already have read the stale cache.

**Result**: `event_generator` kept reading `status="embedding"`, `progress=20%` from `_progress_cache` forever. The `break` condition (`status in ["completed", "failed", "cancelled"]`) was never triggered. The SSE stream looped indefinitely emitting stale progress.

**Fix**: In the failure path, in-memory cache status is now explicitly set to `"failed"` synchronously, *before* any async call:

```python
cache = _jm._progress_cache.get(project_id)
if cache:
    cache["status"] = "failed"
    cache["current_stage"] = f"Failed: {str(e)[:120]}"
    cache["updated_at"] = time.time()
# THEN call _sync_fail_job
_sync_fail_job(project_id, str(e))
```

---

### Bug 2 — No `processed_candidates` / `total_candidates` in SSE payload from DB fallback

When the in-memory cache was absent and the DB fallback was used, the emitted object was missing `processed_candidates` and `total_candidates`. The frontend showed `0/0 candidates` even when work was happening.

**Fix**: Both fields are now included in the DB fallback object with default value `0`.

---

### Bug 3 — No heartbeat; Nginx/Render closes idle SSE connections

The `event_generator` emitted one event every 2 seconds while the worker was running — but if the worker was computing a slow batch (model download, large encode), there could be 60–300 seconds of silence. Render's proxy and Nginx both have idle connection timeouts (typically 60s) that close silent connections.

**Fix**: A comment-line ping is sent every 5 seconds:

```python
yield ": heartbeat\n\n"
```

SSE comment lines are ignored by browsers but keep TCP connections alive through proxies.

---

### Bug 4 — No `Cache-Control` or `X-Accel-Buffering` headers

Without `X-Accel-Buffering: no`, Nginx on Render buffers SSE responses, causing events to arrive in batches or not at all until the buffer fills. Without `Cache-Control: no-cache`, intermediate proxies may cache the stream.

**Fix**: Both headers are now set on the `StreamingResponse`:

```python
headers={
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}
```

---

### Bug 5 — Frontend never reconnected after `onerror`

**Pre-fix frontend code**:

```typescript
eventSource.onerror = (err) => {
  console.error('EventSource error, closing stream:', err);
  eventSource.close();  // ← closes and never reconnects
};
```

When Render's proxy closed the connection (idle timeout, deploy restart, backend hiccup), the frontend permanently lost the progress stream. The UI froze at whatever progress was last displayed.

**Fix**: The frontend now reconnects with exponential backoff:

```typescript
es.onerror = () => {
  es?.close();
  if (closed) return;
  reconnectTimer = setTimeout(() => {
    reconnectDelay = Math.min(reconnectDelay * 1.5, 15000); // cap at 15s
    connect();
  }, reconnectDelay);
};
```

Initial delay: 2s. Grows by 1.5× per failure. Capped at 15s. Stops reconnecting only when a terminal state event is received or the component unmounts.

---

## Post-Fix SSE Behaviour

| Scenario | Pre-Fix | Post-Fix |
|----------|---------|----------|
| Worker completes successfully | `status=completed` sent, SSE closes | Same ✓ |
| Worker fails with exception | SSE loops forever at `status=embedding` | `status=failed` sent, SSE closes, frontend calls `load()` |
| Proxy closes idle connection | Frontend shows frozen progress permanently | Frontend reconnects within 2–15s |
| Backend deploy restart | Frontend disconnects permanently | Frontend reconnects, reads fresh DB state |
| SSE internal error | Silent disconnect | Error payload sent (`status=failed`), then close |
| Model download (slow) | Connection dropped after ~60s silence | Heartbeat every 5s keeps connection alive |

---

## SSE Message Format

Every emitted event is:

```
data: {"status":"embedding","current_stage":"Generating Embeddings","progress_percentage":42,"processed_candidates":21,"total_candidates":50,"ram_usage":1024.3,"peak_ram":1025.1,"eta":"00:01:23"}\n\n
```

Heartbeat pings (ignored by browser):

```
: heartbeat\n\n
```

Terminal event (sent twice — once when detected, once as closing confirmation):

```
data: {"status":"completed","current_stage":"Completed","progress_percentage":100,...}\n\n
data: {"status":"completed","current_stage":"Completed","progress_percentage":100,...}\n\n
```

---

## Frontend Trigger Condition

The SSE `useEffect` activates only when:

```typescript
project.embedding_status === 'queued' || project.embedding_status === 'processing'
```

When the backend marks the project `embedding_status=completed`, the next `load()` call updates `project.embedding_status` to `"completed"`, which causes the effect to re-run and `setWorkerStatus(null)` — hiding the progress bar. The "Run AI Analysis" button is then enabled.
