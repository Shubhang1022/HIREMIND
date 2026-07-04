# IndexingPipelineAudit.md

## Confirmed Execution Path (Based on Render Logs)

The logs confirm execution proceeds through these stages:

```
[BACKGROUND_TASK_START]        ✓
  Streaming Candidates  10%    ✓
  50 candidates enriched       ✓
  Role indexes uploaded        ✓
  skill_index uploaded         ✓
  ids.json uploaded            ✓
  "Generating Embeddings" 20%  ✓  ← LAST LOG LINE
  ... silence ...
```

## Where Execution Stops

**Between progress=20% and the first embedding batch log.**

The exact line that hangs:

```python
encoder = _get_encoder()   # line ~985 in platform.py (pre-fix)
```

This calls:

```python
_encoder = EmbeddingEncoder(model_name=target_model)
_encoder.load_model()
```

Which calls:

```python
model = SentenceTransformer(self.model_name, device="cpu")
```

`SentenceTransformer.__init__` downloads `BAAI/bge-large-en-v1.5` (~1.3 GB) from HuggingFace Hub if the model is not already cached. **There is no timeout configured on this network call.** On Render, this download can:

1. Hang indefinitely (proxy/firewall timeout with no TCP RST)
2. Take 3–5 minutes (model download on slow network)
3. Fail silently after Render's 30s request timeout kills the associated response (but the background thread keeps waiting)

## What Happens After the Hang

The outer `except Exception as e:` at the retry loop catches the eventual failure (or timeout). But:

1. **The full traceback was not logged** — only `[BACKGROUND_TASK_FAIL] Attempt N/3 failed: <message>`. No stage name, no stack trace.
2. **`_sync_fail_job` was called BEFORE updating the in-memory cache**. This means `_progress_cache[project_id]["status"]` remained `"embedding"`, so the SSE stream kept looping and never sent `status=failed`.
3. **The SSE stream looped on stale in-memory cache** with `status="embedding"`, progress=20%, forever — because `event_generator` checks `status_info["status"] in ["completed", "failed", "cancelled"]` to decide when to stop, but the in-memory cache never got updated to a terminal state.
4. **Frontend `eventSource.onerror` closed the stream** eventually (when the backend's SSE response timed out or was reset), but never reconnected because the old code did `eventSource.close()` and returned — no reconnect logic.

## Pipeline Stages Map (Post-Fix)

| Stage | Log Tag | Progress Before | Progress After |
|-------|---------|----------------|----------------|
| Start task | `[BACKGROUND_TASK_START]` | — | — |
| Stream + enrich candidates | `[STAGE_START] stage=stream_candidates` (heartbeat every 5s) | 5% | 20% |
| Upload role/skill/ids indexes | `[STAGE_START] stage=upload_indexes` | 20% | 20% |
| Load embedding model | `[STAGE_START] stage=load_model` | 20% | 25% |
| Generate embeddings + FAISS | `[STAGE_START] stage=generate_embeddings` | 25% | 80% |
| Write .npy file | `[STAGE_START] stage=write_npy` | 80% | 85% |
| Serialize FAISS index | `[STAGE_START] stage=build_faiss` | 85% | 85% |
| Upload all artifacts | `[STAGE_START] stage=upload_artifacts` | 85% | 90% |
| Validate artifacts | `[STAGE_START] stage=validate_artifacts` | 90% | 90% |
| Mark completed | `[STAGE_START] stage=mark_completed` | 90% | 100% |

## Exception Isolation

Every stage is now wrapped independently:

```python
try:
    <stage code>
except Exception as stage_exc:
    logger.exception("[STAGE_FAIL] project=%s stage=<name> elapsed=%.2fs ...", ...)
    raise  # ← always re-raises so retry loop handles it
```

This guarantees:
- Every failure produces a log line with stage name, elapsed time, RAM, and full traceback
- The retry loop counts all failures correctly
- On final failure, the in-memory cache is updated to `"failed"` BEFORE `_sync_fail_job` is called
