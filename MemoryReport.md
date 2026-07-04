# MemoryReport.md — Memory Management

## Memory Sources

| Source | Size | Lifecycle |
|--------|------|-----------|
| FastAPI + uvicorn baseline | ~100 MB | Permanent |
| sentence-transformers library | ~80 MB | Permanent once imported |
| bge-base-en-v1.5 weights | ~300 MB | Permanent (singleton) |
| Candidate JSONL in temp dir | ~0.5 MB per 1k candidates | Freed in `finally` block |
| Raw embeddings `.raw` file | disk only (streamed) | Freed in `finally` block |
| `.npy` assembled file | disk only | Freed in `finally` block |
| FAISS index in RAM | ~4 MB per 1k candidates (768-dim) | Freed after upload |
| Batch tensors (32 × 768 float32) | ~0.1 MB | Released per batch |
| Supabase client | ~5 MB | Permanent |
| Ranking cache | ~1 MB per ranking | In-memory, 2-day TTL |

---

## Memory Safety Mechanisms

### 1. Embedding memory monitor thread

Runs every 10 seconds during the embedding loop:

```python
[EMBEDDING_MONITOR] project=<id> batch=2/16 processed=64/500
                    RSS=452.1MB CPU=84.3% threads=12
```

If `RSS > EMBEDDING_MEM_ABORT_MB` (env var, default 480 MB):

```python
[EMBEDDING_ABORT] project=<id> RSS=488MB exceeds threshold=480MB
                  — aborting embedding to prevent OOM kill
```

The cancellation token is set. The worker loop checks it every 10 candidates and exits cleanly.

### 2. Analysis memory guard (unchanged)

In `run_analysis()`:

```python
if get_memory_mb() > 450.0:
    # Falls back to metadata-only ranking
```

### 3. `finally` block cleanup

After every indexing run (success or failure):

```python
CacheService.invalidate_project(project_id)
shutil.rmtree(temp_dir)         # temp JSONL, .raw, .npy files
gc.collect()
```

The FAISS index object is deleted after serialisation and upload. The `.npy` bytes are deleted after upload (`del npy_content`). Enriched candidate bytes are deleted after upload (`del enriched_content`).

### 4. Model singleton — one load, never unloaded

The model is loaded once and stays in RAM. `reset()` in `model_service.py` exists only for testing — it is never called during normal operation.

---

## Memory Diagnostics Log Points

| Log tag | When emitted |
|---------|-------------|
| `[MEMORY_DIAGNOSTICS] PRE_MODEL_LOAD` | Before `SentenceTransformer()` call |
| `[MEMORY_DIAGNOSTICS] POST_MODEL_LOAD` | After model stored in `_MODEL_CACHE` |
| `[EMBEDDING_MONITOR]` | Every 10s during embedding loop |
| `[EMBEDDING_ABORT]` | If RSS exceeds abort threshold |
| `[DEPLOYMENT_DIAGNOSTICS] STARTUP` | At FastAPI startup |
| `[DEPLOYMENT_DIAGNOSTICS] SHUTDOWN` | At FastAPI shutdown |
| `[STARTUP_SUMMARY]` | rss + avail_ram in human-readable table |

---

## Render Free Tier vs Standard

| Tier | RAM | bge-base | bge-large | Recommendation |
|------|-----|---------|---------|----------------|
| Free (512 MB) | 512 MB | ⚠ Marginal (~450 MB peak) | ❌ OOM | Use bge-base, set persistent disk |
| Starter (1 GB) | 1 GB | ✅ Safe | ⚠ Marginal | bge-base recommended |
| Standard (2 GB) | 2 GB | ✅ Comfortable | ✅ Safe | Recommended for production |

**Current default**: `BAAI/bge-base-en-v1.5` (438 MB) — fits Render free tier with ~60 MB headroom.

To use the large model on Standard tier: set `EMBEDDING_MODEL_NAME=BAAI/bge-large-en-v1.5`.

---

## Configurable Memory Thresholds

| Env var | Default | Purpose |
|---------|---------|---------|
| `EMBEDDING_MEM_ABORT_MB` | `480` | RSS threshold to abort embedding gracefully |
| `MODEL_LOAD_TIMEOUT` | `120` | Seconds before model load is abandoned |
| `WATCHDOG_TIMEOUT_MINUTES` | `2` | Minutes of no heartbeat before job is killed |
