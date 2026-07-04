# ProductionFixReport.md — Indexing Pipeline Fix Summary

## Files Changed

| File | Change type |
|------|-------------|
| `backend/app/api/v1/endpoints/platform.py` | Per-stage instrumentation, exception isolation, SSE heartbeat + headers, failure path fix |
| `frontend/src/app/(dashboard)/projects/[id]/page.tsx` | SSE auto-reconnect with exponential backoff |

---

## Fix 1 — Per-stage instrumentation and exception isolation

**Before**: The entire embedding block (model load + all batches + FAISS build + all uploads) was inside one `try/except`. Any failure anywhere produced a single log line with no stage attribution, no traceback, no RAM value.

**After**: Every named stage is wrapped independently. Each stage emits:
- `[STAGE_START]` with stage name, relevant counters, RAM
- `[STAGE_END]` with elapsed time and RAM  
- `[STAGE_FAIL]` with `logger.exception()` (full traceback) and all context on failure

Stages instrumented:
1. `upload_indexes` — role/skill/ids files
2. `load_model` — SentenceTransformer download + init
3. `generate_embeddings` — batch encode loop + FAISS add
4. `write_npy` — assemble numpy file from raw bytes
5. `build_faiss` — serialize FAISS index
6. `upload_artifacts` — enriched JSONL + .npy + .index to storage
7. `validate_artifacts` — file_exists checks
8. `mark_completed` — DB updates + progress=100

---

## Fix 2 — `[PIPELINE_TIMEOUT]` detection for model load

If `load_model` takes more than 60 seconds, a warning is emitted:

```
[PIPELINE_TIMEOUT] project=<id> stage=load_model elapsed=187.3s ram=210.4MB — model load exceeded 60s
```

This does not abort the operation — it provides visibility. After this log line, the model either succeeds (log shows `[STAGE_END]`) or eventually fails (log shows `[STAGE_FAIL]` with traceback).

---

## Fix 3 — In-memory cache updated before `_sync_fail_job`

**Before**:
```python
_sync_fail_job(project_id, str(e))
# cache["status"] still = "embedding"
# SSE reads "embedding" → loops forever
```

**After**:
```python
# Update in-memory cache synchronously FIRST
cache = _jm._progress_cache.get(project_id)
if cache:
    cache["status"] = "failed"
    cache["current_stage"] = f"Failed: {str(e)[:120]}"
    cache["updated_at"] = time.time()
# NOW schedule the DB update
_sync_fail_job(project_id, str(e))
# SSE reads "failed" on next 2s poll → sends terminal event → breaks loop
```

---

## Fix 4 — Failure path logs full traceback

**Before**:
```
[BACKGROUND_TASK_FAIL] Attempt 1/3 failed: <error message>
```

**After**:
```
[BACKGROUND_TASK_FAIL] project=<id> attempt=1/3 elapsed=187.5s ram=210.4MB
Exception: ConnectionTimeout(...)
Traceback (most recent call last):
  File ".../sentence_transformers/SentenceTransformer.py", line 89, ...
  ...
```

The full Python traceback is now included in every retry-loop failure log.

---

## Fix 5 — SSE heartbeat keeps proxy connections alive

**Before**: `event_generator` emitted events only when the worker updated the cache. During model download (60–300s), no data was sent and proxies timed out.

**After**:
```python
if now - last_heartbeat >= HEARTBEAT_INTERVAL:  # 5 seconds
    yield ": heartbeat\n\n"
    last_heartbeat = now
```

SSE comment lines are invisible to the browser but keep the TCP connection alive through Render's proxy.

---

## Fix 6 — SSE response headers prevent Nginx buffering

**Before**: `StreamingResponse(event_generator(), media_type="text/event-stream")`

**After**:
```python
StreamingResponse(
    event_generator(),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # disables Nginx buffering on Render
    },
)
```

---

## Fix 7 — SSE error handling: terminal event before close

**Before**: If `event_generator` threw an exception, the generator stopped with no event. The frontend saw a connection drop.

**After**: Any exception inside the SSE loop sends a `status=failed` payload before breaking:

```python
except Exception as sse_exc:
    error_payload = json.dumps({"status": "failed", "current_stage": f"SSE error: {str(sse_exc)[:80]}", ...})
    yield f"data: {error_payload}\n\n"
    break
```

---

## Fix 8 — Frontend SSE auto-reconnect with exponential backoff

**Before**:
```typescript
eventSource.onerror = (err) => {
  eventSource.close();  // closes permanently — no reconnect
};
```

**After**:
```typescript
es.onerror = () => {
  es?.close();
  if (closed) return;
  reconnectTimer = setTimeout(() => {
    reconnectDelay = Math.min(reconnectDelay * 1.5, 15000);
    connect();  // reconnect with backoff
  }, reconnectDelay);
};
```

- Initial reconnect delay: 2 seconds
- Multiplier: 1.5×  
- Cap: 15 seconds
- Stops when: terminal event received OR component unmounts

---

## Fix 9 — Zero-division guard in progress formula

**Before**: `progress_pct = 20 + int(global_idx / total_candidates * 60)`

**After**: `progress_pct = 25 + int(global_idx / max(total_candidates, 1) * 55)`

Also changed the progress range from 20–80 to 25–80 to account for the new `load_model` checkpoint at 25%.

---

## Success Criteria Verification

| Step | Pre-Fix | Post-Fix |
|------|---------|----------|
| Progress past 20% | ❌ Stuck forever | ✅ Advances per stage (25%→80%→85%→90%→100%) |
| Model load failure logged | ❌ Opaque `Attempt N failed` message | ✅ `[STAGE_FAIL] stage=load_model` with full traceback |
| SSE terminal event on failure | ❌ Never sent; stream loops | ✅ `status=failed` sent within 2s of failure |
| SSE reconnect after drop | ❌ Permanently disconnected | ✅ Auto-reconnects within 2–15s |
| Proxy idle timeout | ❌ Connection dropped after 60s silence | ✅ Heartbeat every 5s keeps alive |
| Render log shows exact failure point | ❌ No stage information | ✅ Every stage has START/END/FAIL log |
| UI enables "Run Analysis" after completion | ❌ Never reached | ✅ After `status=completed`, `load()` fires, button enabled |
