# PerformanceImprovements.md

## Improvements Applied in This Session

---

### 1. Embedding model: bge-large → bge-small (3× size reduction)

| Model | Size | Dim | Render free tier |
|-------|------|-----|-----------------|
| bge-large-en-v1.5 | 1.34 GB | 1024 | ❌ OOM |
| bge-base-en-v1.5  | 438 MB  | 768  | ❌ OOM |
| bge-small-en-v1.5 | 90 MB   | 384  | ✅ Safe |

RSS impact: ~900 MB reduction. The primary reason for Render OOM kills was loading bge-large.

---

### 2. Model pre-baked into Docker image (zero cold-start download)

```dockerfile
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('BAAI/bge-small-en-v1.5', device='cpu')"
```

On cold start, `model_service._do_load()` hits `_MODEL_CACHE` immediately (cache hit path) — the model is loaded from disk in `~5–15s` instead of downloading `~438 MB` (40–80s minimum).

---

### 3. Non-blocking startup: model preloads in daemon thread

`preload_model_singleton()` starts a daemon thread that loads the model. FastAPI yields immediately and accepts requests. The first upload request does not block — if the model isn't ready yet, `get_model()` waits with heartbeat logs every 5s.

---

### 4. Background monitoring thread during embedding (5-second interval)

The `_embedding_memory_monitor` thread logs every 5 seconds:
- RSS, CPU%, active threads
- Current batch / total batches
- Remaining candidates

If RSS exceeds 85% of `EMBEDDING_MEM_ABORT_MB` (default 480 MB): logs `[HIGH_MEMORY_WARNING]`.
If RSS exceeds limit: sets cancellation token → embedding stops cleanly instead of OOM kill.

---

### 5. Per-batch progress reporting (continuous, not jumping)

| Before | After |
|--------|-------|
| 20% → 100% (two data points) | 20% → 25% → 28% → … → 78% → 85% → 90% → 100% |
| No batch-level visibility | `[EMBEDDING_BATCH]` log per batch with speed, elapsed, RAM |

Progress formula: `25 + int(global_idx / max(total_candidates, 1) * 53)` — distributes 53% across all batches.

---

### 6. Stage checkpointing with skip-on-retry

```
upload_indexes ✓  →  checkpoint saved
load_model     ✓  →  checkpoint saved
generate_embeddings ✓  →  checkpoint saved
```

On retry, stages before `last_done` are skipped entirely. `upload_indexes` (3 Supabase storage writes) is never repeated if it already succeeded.

---

### 7. FAISS dimension validated before search

```python
if idx_dim != enc_dim:
    raise HTTPException(409, "INDEX_DIMENSION_MISMATCH — re-upload candidates")
```

Previously, a mismatched index caused `src/ranking/engine.py` to silently reload `bge-large` (1.34 GB → OOM). Now it fails fast with a 409 and a user-readable message.

---

### 8. Memory cleanup improvements

Each upload stage deletes byte buffers immediately after use:
```python
del enriched_content   # after upload to storage
del npy_content        # after upload to storage
del faiss_content      # after serialize + upload
```

The `finally` block in `process_project_data_task` calls `shutil.rmtree(temp_dir)` and `gc.collect()`.

---

### 9. 30-second worker heartbeat

Daemon thread logs `[WORKER_HEARTBEAT]` every 30s:
- RSS, CPU, thread count
- Pending asyncio tasks
- Event loop state
- Uptime

Operators can confirm the worker is alive between requests without hitting endpoints.

---

### 10. Deferred startup checks (non-blocking)

`run_startup_check()` (makes 5 Supabase queries + 1 storage probe) now runs in `_deferred_startup()` after a `0.5s asyncio.sleep`. The API is ready in ~50ms instead of 1–3s.

---

## Memory Profile (Expected, bge-small, Render free tier 512 MB)

| Phase | RSS |
|-------|-----|
| Process start | ~80 MB |
| FastAPI + imports | ~180 MB |
| After `yield` (API ready) | ~182 MB |
| Model loaded (bge-small) | ~272 MB |
| During embedding (50 candidates) | ~278 MB |
| After GC + cleanup | ~274 MB |
| Safety headroom | ~238 MB remaining |

---

## Embedding Speed (bge-small, CPU, 50 candidates)

| Batch size | Typical speed | Notes |
|-----------|--------------|-------|
| 32 | 40–80 candidates/s | CPU only, no GPU |
| 50 total | ~1–2 batches, <5s | Well within 60s stage timeout |
