# MemoryDiagnostics.md — Memory Diagnostics

## Log Format

Memory is logged at every significant stage transition using:

```python
logger.info(
    "[MEMORY_DIAGNOSTICS] %s | RSS=%.1fMB VMS=%.1fMB AvailRAM=%.1fMB CPU=%.1f%%",
    label, rss, vms, avail, cpu,
)
```

Labels emitted during model lifecycle:

| Label | When |
|-------|------|
| `PRE_MODEL_LOAD` | Immediately before `SentenceTransformer()` call |
| `POST_MODEL_LOAD` | Immediately after model is stored in `_MODEL_CACHE` |

Labels emitted during indexing pipeline (existing):

| Label | When |
|-------|------|
| `[STAGE_START] stage=load_model` | Before calling `_get_encoder()` |
| `[STAGE_END] stage=load_model` | After `_get_encoder()` returns |
| `[WORKER_HEARTBEAT]` | Every 5s during candidate streaming |

---

## Expected Memory Profile

### Render Free Tier (512 MB RAM)

| Phase | RSS | Notes |
|-------|-----|-------|
| Startup (bare FastAPI) | ~180 MB | Before model load |
| After `bge-base-en-v1.5` loads | ~450 MB | Just within free tier limit |
| During encoding (50 candidates) | ~460 MB | +10 MB for batch tensors |
| After GC in finally block | ~450 MB | Model remains resident |

### Render Standard (2 GB RAM) — recommended

| Phase | RSS | Notes |
|-------|-----|-------|
| Startup (bare FastAPI) | ~180 MB | |
| After `bge-base-en-v1.5` loads | ~450 MB | 75% headroom remaining |
| After `bge-large-en-v1.5` loads | ~1400 MB | Fits with headroom |
| During encoding (10k candidates) | ~1500 MB | |

---

## Safety Threshold Check

The existing `run_analysis()` memory guard remains unchanged:

```python
if get_memory_mb() > 450.0:
    # metadata-only fallback
```

This guard fires when the process is near free-tier limits. With `bge-base` the threshold
is rarely crossed. With `bge-large` it was crossed on every run.

---

## Pre-Load Memory Diagnostic (from model_service.py)

```
[MEMORY_DIAGNOSTICS] PRE_MODEL_LOAD | RSS=182.3MB VMS=1024.0MB AvailRAM=318.7MB CPU=2.1%
[MODEL_SERVICE] [MODEL_CACHE_MISS] name=BAAI/bge-base-en-v1.5 — downloading/loading
[MODEL_SERVICE] [MODEL_LOAD_HEARTBEAT] waited=5s/120s model=BAAI/bge-base-en-v1.5 ram=210.3MB
[MODEL_SERVICE] [MODEL_LOAD_HEARTBEAT] waited=10s/120s model=BAAI/bge-base-en-v1.5 ram=240.1MB
...
[MODEL_SERVICE] [MODEL_LOAD_COMPLETE] name=BAAI/bge-base-en-v1.5 elapsed=42.1s
[MEMORY_DIAGNOSTICS] POST_MODEL_LOAD | RSS=449.2MB VMS=1248.0MB AvailRAM=62.8MB CPU=85.3%
```

---

## OOM Prevention

If `AvailRAM` at `PRE_MODEL_LOAD` is < 200 MB, the download risks killing the process.

The model_service does not currently abort on low memory — this is by design because
the check would be a false positive when the model is already downloading in another thread.
The `MODEL_LOAD_TIMEOUT` of 120s serves as the safety valve: if the download stalls
(commonly because the OS swaps under memory pressure), the timeout fires and the job is
marked `failed` cleanly instead of being killed by the OOM killer.

**Recommendation**: Set `MODEL_LOAD_TIMEOUT=90` (env var) on Render free tier to fail
fast before Render's health check timeout (typically 3 minutes) kills the process.
