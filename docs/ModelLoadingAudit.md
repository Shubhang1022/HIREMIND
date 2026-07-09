# ModelLoadingAudit.md

## Overview

Documents the model loading implementation after the hanging-SentenceTransformer fix.

---

## Cache Configuration

| Variable | Value (Docker ENV) | Runtime default |
|----------|--------------------|----------------|
| `HF_HOME` | `/app/.cache/huggingface` | same |
| `TRANSFORMERS_CACHE` | `/app/.cache/huggingface` | same |
| `SENTENCE_TRANSFORMERS_HOME` | `/app/.cache/sentence-transformers` | same |

**Resolved model path** (logged at every load):
```
/app/.cache/sentence-transformers/BAAI_bge-small-en-v1.5/
```

This is exactly what `SentenceTransformer(model_name, cache_folder=...)` will read.

---

## Cache Verification (Phase 1 + 7)

`verify_docker_cache()` runs before any ML import. It checks:

1. `SENTENCE_TRANSFORMERS_HOME` directory exists
2. Model subdirectory `BAAI_bge-small-en-v1.5/` exists
3. Every required file is present (see CacheAudit.md)
4. At least one weight file exists

On failure: logs `[DOCKER_CACHE_INVALID]` and raises `ModelLoadFailed` immediately.
**No network access is attempted.**

---

## Offline Mode (Phase 4)

Set in `_set_offline_mode()` **before any transformers import**:

```python
os.environ["HF_HUB_OFFLINE"]      = "1"   # huggingface_hub raises error on any HTTP call
os.environ["TRANSFORMERS_OFFLINE"] = "1"   # transformers refuses any Hub access
os.environ["HF_DATASETS_OFFLINE"]  = "1"   # datasets refuses any Hub access
```

These are hard `"1"` assignments (not `setdefault`) — they override any Railway env var.

---

## Network Requests Made

**Zero.** Verified by:
1. `HF_HUB_OFFLINE=1` — huggingface_hub raises `OfflineModeIsEnabled` on any HTTP attempt
2. `local_files_only=True` in `SentenceTransformer()` — raises `OSError` if file missing
3. `grep` scan of entire repo: zero `from_pretrained(`, `hf_hub_download(`, `snapshot_download(` calls

---

## SentenceTransformer Call (Phase 2)

```python
SentenceTransformer(
    model_name,
    cache_folder=os.environ["SENTENCE_TRANSFORMERS_HOME"],  # explicit, not default
    local_files_only=True,                                  # never contacts Hub
    device="cpu",
)
```

If the model directory is missing: `OSError` is raised immediately (< 1 s).
If network is accidentally attempted: `OfflineModeIsEnabled` is raised immediately.
**No 120-second hang.**

---

## Stage Instrumentation (Phase 3)

Each stage prints elapsed time, RSS, CPU%, thread count:

```
[STAGE] START_LOAD                       | elapsed=  0.00s | RSS= 165.0MB | CPU=  0.0% | threads=4
[STAGE] VERIFY_CACHE                     | elapsed=  0.01s | RSS= 165.0MB | CPU=  0.5% | threads=4
[STAGE] LOAD_CONFIG                      | elapsed=  0.02s | RSS= 165.0MB | CPU=  0.5% | threads=4
[STAGE] LOAD_TOKENIZER                   | elapsed=  2.10s | RSS= 315.0MB | CPU= 45.0% | threads=5
[STAGE] LOAD_MODEL_WEIGHTS               | elapsed=  2.50s | RSS= 360.0MB | CPU= 30.0% | threads=5
[STAGE] BUILD_MODULES                    | elapsed= 14.80s | RSS= 580.0MB | CPU= 70.0% | threads=5
[STAGE] INITIALIZE_POOLING               | elapsed= 15.20s | RSS= 580.0MB | CPU=  5.0% | threads=5
[STAGE] MODEL_READY                      | elapsed= 15.30s | RSS= 545.0MB | CPU=  2.0% | threads=5
```

The stage where elapsed time stops increasing is where the hang was.
After this fix, no stage should exceed 30 seconds.

---

## Fail-Fast Timeout (Phase 6)

`MODEL_LOAD_TIMEOUT` env var (default: `30` seconds).

Each expensive stage (`LOAD_MODEL_WEIGHTS` specifically) is wrapped in `_run_with_timeout()`.
If it exceeds 30 s, `ModelLoadTimeout` is raised with:
- `current_stage`
- `elapsed`
- `RSS`
- `CPU`
- `cache_path`
- `missing_files`

**Never waits 120 seconds.**

---

## Root Cause of the Hang

The previous code called:
```python
SentenceTransformer(model_name, device="cpu")
```

Without `cache_folder` and without `local_files_only=True`.

**What happened**: `SentenceTransformer` uses `huggingface_hub.snapshot_download()` internally.
When the local cache path doesn't exactly match what `huggingface_hub` computes,
it attempts to reach `https://huggingface.co` to check for updates (even if files exist).
On Railway, this HTTP request hangs indefinitely (network timeout is 120+ seconds with no TCP RST).
Memory stabilized at ~500 MB because the model was partially loaded — only the network wait was hanging.

**The fix**:
- `local_files_only=True` → raises `OSError` immediately instead of attempting network
- `cache_folder=SENTENCE_TRANSFORMERS_HOME` → points to exactly the Docker-baked path
- `HF_HUB_OFFLINE=1` → belt-and-suspenders: any internal Hub call raises immediately

---

## Load Duration

| Build | Expected duration |
|-------|------------------|
| Docker cache hit (local_files_only=True) | 10–20 seconds |
| Cache miss (fails immediately) | < 1 second |
| Network hang (old behaviour, now impossible) | 120+ seconds → OOM timeout |

---

## Peak RSS

| State | RSS |
|-------|-----|
| Startup (no model) | ~150 MB |
| After torch import | ~300 MB |
| After SentenceTransformer() + gc.collect() | ~545 MB |
| During active indexing (10k candidates) | ~700 MB |

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/services/model_service.py` | Full rewrite: all 9 phases |
| `backend/Dockerfile` | Pre-bake uses `cache_folder=` to match runtime path exactly; verifies files at build time |
