# StartupTimeline.md

Timestamps are relative to process start (`t=0s`).  
All log tags shown are emitted to stdout and Railway logs.

---

## Phase A — API Ready (no model loaded)

```
t=0.0s   [WORKER_STARTED] pid=...
         Process start. RSS ~74 MB.

t=0.1s   Module-level imports execute:
         FastAPI, pydantic, supabase, numpy, psutil
         RSS ~130 MB.

t=0.5s   lifespan() reaches yield
         [STARTUP_PERF] RSS=~130 MB  elapsed=0.5s (model NOT preloaded)
         [STARTUP] Model preload skipped — lazy load mode active.
         [WORKER_READY] pid=...
         ✅ Server accepts HTTP requests.
         Railway health check responds 200 at this point.
```

---

## Phase B — First Upload (user submits candidate file)

```
t=varies   POST /api/v1/platform/projects/{id}/upload-candidates
           upload handler receives file → saves to temp path
           spawns background thread: process_candidate_upload_task()
           ↓
           process_candidate_upload_task() → parse candidates → upload to Supabase
           ↓
           registers indexing job → calls process_project_data_task()
```

---

## Phase C — Cache Verified

```
t=varies+0.0s  _get_encoder() called from process_project_data_task()
               → model_service.get_model()
               → _load_state == "unloaded"

               [MODEL_SERVICE] [LAZY_LOAD_TRIGGERED] model=BAAI/bge-small-en-v1.5

               _do_load() starts in daemon thread:

               [MODEL_SERVICE] HF offline mode ENABLED
               [MODEL_SERVICE] Thread limits: OMP=1 MKL=1 ...

               [STAGE] START_LOAD       | elapsed=0.01s | RSS=165MB

               [MODEL_CACHE_VERIFY] ──────────────────────────
                 HF_HOME                   : /app/.cache/huggingface
                 TRANSFORMERS_CACHE        : /app/.cache/huggingface
                 SENTENCE_TRANSFORMERS_HOME: /app/.cache/sentence-transformers
                 model_name                : BAAI/bge-small-en-v1.5
                 model_dir (resolved)      : /app/.cache/sentence-transformers/BAAI_bge-small-en-v1.5
                 model_dir_exists          : True
               [MODEL_CACHE_VERIFY] CACHE_OK
                 files    : [config.json, model.safetensors, modules.json, ...]
                 total_size: 87.3 MB
                 missing  : []

               [STAGE] VERIFY_CACHE     | elapsed=0.05s | RSS=165MB
               ✅ Cache verified.
```

---

## Phase D — Model Loaded

```
               [STAGE] LOAD_CONFIG      | elapsed=0.06s | RSS=165MB
               — import torch (CPU build ~150 MB)
               [TORCH_DIAGNOSTICS] version=2.x.x+cpu cuda=None ...
               [STAGE] LOAD_TOKENIZER   | elapsed=2.1s  | RSS=315MB
               — import sentence_transformers

               [STAGE] LOAD_MODEL_WEIGHTS | elapsed=2.5s | RSS=360MB
               [MODEL_SERVICE] Resolved model path: /app/.cache/.../BAAI_bge-small-en-v1.5
               [MODEL_SERVICE] cache_folder: /app/.cache/sentence-transformers
               [MODEL_SERVICE] local_files_only: True
               — SentenceTransformer() reads weights from disk only
               — No network requests (HF_HUB_OFFLINE=1, local_files_only=True)

               [STAGE] BUILD_MODULES    | elapsed=14.8s | RSS=580MB
               [STAGE] INITIALIZE_POOLING | elapsed=15.2s | RSS=580MB

               [MODEL_SERVICE] [MODEL_LOAD_COMPLETE]
                 model=BAAI/bge-small-en-v1.5  elapsed=15.3s
                 embedding_dim=384  RSS=580MB
               [MODEL_SERVICE] [GC_RESULT] freed=35MB rss_after=545MB
               [MODEL_SERVICE] [MODEL_SINGLETON_CREATED] id=... name=...

               [STAGE] MODEL_READY      | elapsed=15.4s | RSS=545MB
               ✅ Model loaded and ready.
```

---

## Phase E — Embedding Ready

```
               _get_encoder() returns EmbeddingEncoder wrapping the singleton.
               Encoding loop begins: batches of 32 candidates encoded per iteration.

               [BACKGROUND_TASK] Generating Embeddings — progress 20%
               ...
               [MODEL_SERVICE] [MODEL_LOAD_COMPLETE] appears in logs.
               ✅ Embedding pipeline operational.
```

---

## Failure Path — Cache Missing (DOCKER_CACHE_INVALID)

```
               [STAGE] VERIFY_CACHE     | elapsed=0.05s | RSS=165MB

               [MODEL_CACHE_VERIFY] CACHE_INCOMPLETE
                 model_path   : /app/.cache/sentence-transformers/BAAI_bge-small-en-v1.5
                 exists       : False
                 missing      : ['model_dir .../BAAI_bge-small-en-v1.5 (directory missing)']

               [DOCKER_CACHE_INVALID] model=BAAI/bge-small-en-v1.5
                 model_dir=... missing=[...] Rebuild Docker image.

               [MODEL_SERVICE] [MODEL_LOAD_FAILED] stage=VERIFY_CACHE
                 error=ModelLoadFailed: Docker cache incomplete ...

               ❌ Load fails in < 1 second. No hang. No retry. No network attempt.
               Indexing job marked FAILED. Frontend shows error state.
               Fix: rebuild Docker image.
```

---

## Failure Path — Stage Timeout (LOAD_MODEL_WEIGHTS hangs)

```
               [STAGE] LOAD_MODEL_WEIGHTS | elapsed=2.5s | RSS=360MB

               ... 30 seconds pass ...

               [MODEL_LOAD_TIMEOUT] stage=LOAD_MODEL_WEIGHTS exceeded 30s
                 model=BAAI/bge-small-en-v1.5
                 cache_path=/app/.cache/sentence-transformers/BAAI_bge-small-en-v1.5
                 missing_files=[]  RSS=500MB  CPU=0.0%

               ❌ Raises ModelLoadTimeout immediately.
               Caller receives 503 or job marked FAILED.
               Fix: check if SENTENCE_TRANSFORMERS_HOME was overridden in Railway env vars.
```

---

## Timeline Summary Table

| Phase | Label | Condition | Elapsed | RSS |
|-------|-------|-----------|---------|-----|
| A | API Ready | Always | ~0.5 s | ~130 MB |
| B | First Upload | First user upload | varies | ~130 MB |
| C | Cache Verified | Model lazy-load triggered | +0.05 s | ~165 MB |
| D | Model Loaded | Cache hit, disk read | +15 s | ~545 MB |
| E | Embedding Ready | First encode call | +0 s | ~545 MB |
| — | CACHE_INVALID | Docker image broken | +0.05 s | — |
| — | TIMEOUT | Stage hung >30 s | +30 s | — |

---

## Key Log Tags to Monitor on Railway

| Tag | Meaning |
|-----|---------|
| `[STARTUP_PERF] RSS=~130MB` | Startup clean, model not loaded yet ✅ |
| `[LAZY_LOAD_TRIGGERED]` | First embedding request arrived |
| `[MODEL_CACHE_VERIFY] CACHE_OK` | Docker cache valid ✅ |
| `[DOCKER_CACHE_INVALID]` | Docker image needs rebuild ❌ |
| `[STAGE] VERIFY_CACHE` | Cache check started |
| `[STAGE] MODEL_READY` | Model fully loaded ✅ |
| `[MODEL_LOAD_COMPLETE]` | Success — embedding_dim=384 ✅ |
| `[MODEL_LOAD_TIMEOUT]` | Stage exceeded 30 s ❌ |
| `[MODEL_LOAD_FAILED]` | Load raised an exception ❌ |
| `[MODEL_SINGLETON_CREATED]` | Exactly once per process ✅ |
| `[MODEL_REUSED]` | Singleton hit — instant return ✅ |
