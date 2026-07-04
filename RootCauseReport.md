# RootCauseReport.md — Indexing Stuck at 20%: Root Cause

## The Exact Failure Chain

```
1. Upload candidates → background task starts → progress reaches 20%
2. _get_encoder() is called from inside process_project_data_task()
3. _encoder is None (no startup preload) → calls EmbeddingEncoder.load_model()
4. load_model() calls SentenceTransformer("BAAI/bge-large-en-v1.5", device="cpu")
5. HuggingFace Hub starts downloading 1.34 GB of model weights
6. Download stalls (Render free tier network: slow, proxy idle-timeout, no HF cache mounted)
7. SentenceTransformer() has NO download timeout — the call blocks indefinitely
8. Worker thread hangs. No log output. No progress update.
9. Render detects the process as unresponsive (no HTTP traffic, or memory pressure)
10. Render restarts the container.
11. FastAPI lifespan runs → recover_interrupted_jobs() fires
12. DB still shows job status = "embedding" / "processing"
13. retry_count < 3 → job re-queued via asyncio.create_task(_safely_run_indexing)
14. New process starts → _get_encoder() called again
15. Model cache is gone (new process) → re-downloads again → hangs again
16. GOTO step 9. Infinite loop.
```

---

## Root Cause 1 — Wrong model size (LARGE instead of BASE)

**File**: `backend/app/core/config.py` line 83

```python
embedding_model: str = "BAAI/bge-large-en-v1.5"
```

`BAAI/bge-large-en-v1.5` is **1.34 GB** on disk. `BAAI/bge-base-en-v1.5` is **438 MB** — 3× smaller with <3% quality difference for retrieval tasks. On Render's free tier (512 MB RAM) the large model alone exceeds available memory, causing the process to be OOM-killed mid-download.

**No `EMBEDDING_MODEL` or `EMBEDDING_MODEL_NAME` environment variable is set in `.env`**, so the hardcoded large model is always used.

---

## Root Cause 2 — No model preload; model loaded inside background thread

**File**: `backend/app/api/v1/endpoints/platform.py` — `process_project_data_task()`

```python
encoder = _get_encoder()  # called inside the worker thread, after progress=20%
```

`_get_encoder()` calls `load_model()` which calls `SentenceTransformer(model_name, device="cpu")`. This is a blocking synchronous call inside a background thread. There is no timeout on the HTTP download. The thread simply waits forever.

---

## Root Cause 3 — No download timeout on SentenceTransformer

**File**: `src/features/embedding.py` — `load_model()`

```python
model = SentenceTransformer(self.model_name, **kwargs)
```

`SentenceTransformer.__init__` calls `huggingface_hub.snapshot_download()` internally. No timeout is passed. On a slow or stalled network connection, this call blocks for minutes or indefinitely.

---

## Root Cause 4 — Recovery loop recreates the infinite hang

**File**: `backend/app/services/job_manager.py` — `recover_interrupted_jobs()`

```python
if retry_count < 3:
    new_retry = retry_count + 1
    # Update DB to retrying
    asyncio.create_task(self._safely_run_indexing(project_id))
```

This fires on every Render restart. `_safely_run_indexing` calls `process_project_data_task` which calls `_get_encoder()` which hangs. The model cache does not survive restarts (it lives in `src/features/embedding._MODEL_CACHE`, a module-level dict that is empty in every new process). So every restart produces exactly the same hang.

The fix_count check (`retry_count < 3`) provides three restarts worth of retries before permanently failing — but each retry takes as long as the download timeout, and if Render kills the process before any retry completes, `retry_count` is never actually incremented in DB (since the failure path is never reached), so the same job is retried on every restart indefinitely.

---

## Root Cause 5 — HuggingFace cache not persistent on Render

**File**: `src/features/embedding.py` — `load_model()`

```python
os.environ["HF_HOME"] = "/app/.cache/huggingface"
```

`/app/.cache/huggingface` is inside the ephemeral Docker filesystem on Render. It is destroyed on every restart. Unless a persistent disk is mounted at this path, the 1.34 GB model is re-downloaded from scratch on every cold start.

---

## Root Cause 6 — Model instantiated in multiple code paths

Every execution path that needs embeddings creates its own `EmbeddingEncoder` instance or calls `_get_encoder()` independently:

| File | Line | Call |
|------|------|------|
| `backend/app/api/v1/endpoints/platform.py` | ~918 | `encoder = _get_encoder()` inside indexing task |
| `backend/app/api/v1/endpoints/platform.py` | ~2578 | `encoder = _get_encoder()` inside analysis task |
| `backend/app/api/v1/endpoints/platform.py` | ~2741 | `UnifiedRankingEngine(encoder=_get_encoder(), ...)` |
| `src/intelligence/embeddings.py` | 22 | `self._encoder = EmbeddingEncoder(model_name=...)` + `load()` |
| `rank.py` | 178 | `encoder = EmbeddingEncoder(model_name=settings.embedding_model)` |
| `precompute.py` | 141 | `encoder = EmbeddingEncoder(model_name=args.model)` |

The `_MODEL_CACHE` dict in `src/features/embedding.py` prevents double-instantiation **within the same process lifetime**, but does not prevent re-download after restart.

---

## Summary

| # | Severity | Root Cause |
|---|----------|-----------|
| 1 | 🔴 Critical | `bge-large-en-v1.5` (1.34 GB) exceeds Render free-tier RAM; blocks download indefinitely |
| 2 | 🔴 Critical | Model loaded inside background thread with no timeout — hangs silently |
| 3 | 🔴 Critical | Recovery loop retries same hanging job on every restart → infinite loop |
| 4 | 🟠 High | No startup preload — first upload always cold-loads the model |
| 5 | 🟠 High | HF cache at `/app/.cache/` is ephemeral — re-downloaded on every restart |
| 6 | 🟡 Medium | Multiple instantiation sites risk repeated loads if cache is cleared |
