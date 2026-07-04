# ModelLifecycle.md — Embedding Model Lifecycle

## Invariants (enforced by code)

1. `SentenceTransformer()` is called exactly **once** per process lifetime — in `model_service._do_load()`.
2. Every subsequent call to `get_model()` returns the cached instance without any I/O.
3. `[MODEL_SINGLETON_CREATED]` is logged exactly once (on first successful load).
4. `[MODEL_REUSED]` is logged at `DEBUG` level on every fast-path `get_model()` call.
5. `[MODEL_CACHE_HIT]` is logged once if the model was found in `_MODEL_CACHE` on first call.
6. `[MODEL_CACHE_MISS]` is logged once if the model needed to be downloaded.
7. `_cache_verdict_logged` flag prevents duplicate CACHE_HIT/MISS log lines.

---

## Lifecycle Diagram

```
FastAPI starts
      │
      ▼
preload_model_singleton()  ← called in lifespan, returns immediately
      │
      ▼ (daemon thread)
_do_load("BAAI/bge-base-en-v1.5")
      │
      ├── Check _MODEL_CACHE
      │       ├── HIT  →  [MODEL_CACHE_HIT]  →  _load_state="loaded"  →  _load_event.set()
      │       └── MISS →  [MODEL_CACHE_MISS] →  SentenceTransformer(model, device="cpu")
      │                           │
      │                           ├── success → [MODEL_LOAD_COMPLETE]
      │                           │             [MODEL_SINGLETON_CREATED]
      │                           │             _load_state="loaded"
      │                           │             _load_event.set()
      │                           │
      │                           └── failure → [MODEL_LOAD_FAILED] (full traceback)
      │                                         _load_state="failed"
      │                                         _load_event.set()
      │
      ▼
First indexing job arrives
      │
      ▼
_get_encoder()
      │
      ├── is_loaded() == True  →  [MODEL_CACHE_HIT] (in platform.py stage log)
      │                           get_model()  →  [MODEL_REUSED] (debug)
      │                           wrap in EmbeddingEncoder  →  return
      │
      └── is_loaded() == False →  get_model(timeout=120)
                                      waits with [MODEL_LOAD_HEARTBEAT] every 5s
                                      returns when loaded
                                      (or raises ModelLoadTimeout after 120s)

Every subsequent call to _get_encoder():
      └── fast path: _encoder is not None, _encoder._model is not None  →  return immediately
                     [MODEL_REUSED] logged at DEBUG level
```

---

## Log Sequence (normal cold-start)

```
[MODEL_SERVICE] Starting background preload for model=BAAI/bge-base-en-v1.5
[MEMORY_DIAGNOSTICS] PRE_MODEL_LOAD | RSS=182.3MB VMS=... AvailRAM=...
[MODEL_SERVICE] [MODEL_CACHE_MISS] name=BAAI/bge-base-en-v1.5 — downloading/loading
... (no logs for 40-80s while downloading)
[MODEL_SERVICE] [MODEL_LOAD_COMPLETE] name=BAAI/bge-base-en-v1.5 elapsed=52.1s
[MODEL_SERVICE] [MODEL_SINGLETON_CREATED] name=BAAI/bge-base-en-v1.5
[MEMORY_DIAGNOSTICS] POST_MODEL_LOAD | RSS=450.2MB ...
```

Then on first indexing:

```
[MODEL_SERVICE] [MODEL_CACHE_HIT] model already loaded — skipping download
[STAGE_END] project=... stage=load_model elapsed=0.001s ram=450.2MB dim=768
```

Then on every subsequent indexing:

```
[MODEL_SERVICE] [MODEL_REUSED] name=BAAI/bge-base-en-v1.5   ← DEBUG level
```

---

## Log Sequence (timeout — no network)

```
[MODEL_SERVICE] Starting background preload for model=BAAI/bge-base-en-v1.5
[MODEL_SERVICE] [MODEL_CACHE_MISS] name=... — downloading/loading
[MODEL_SERVICE] [MODEL_LOAD_HEARTBEAT] waited=5s/120s model=... ram=182.1MB
[MODEL_SERVICE] [MODEL_LOAD_HEARTBEAT] waited=10s/120s ...
[MODEL_SERVICE] [MODEL_LOAD_HEARTBEAT] waited=...
...
[MODEL_SERVICE] [MODEL_LOAD_TIMEOUT] model=BAAI/bge-base-en-v1.5 timeout=120s
[STAGE_FAIL] project=... stage=load_model error=ModelLoadTimeout(...)
[BACKGROUND_TASK_FINAL_FAIL] project=... marking failed
  failure_reason = "MODEL_LOAD_FAILED: Model '...' did not finish loading within 120s"
```

On next restart:
```
[RECOVERY] Permanently failing job ... for project ...: Non-retryable failure: model load error.
[RECOVERY_SUMMARY] recovered=0 permanent_failures=1
```

No infinite loop.

---

## Model Configuration Priority

```
EMBEDDING_MODEL_NAME env var
  └── if set, overrides everything

EMBEDDING_MODEL env var (legacy)
  └── if set and EMBEDDING_MODEL_NAME not set

settings.embedding_model (config.py)
  └── default: "BAAI/bge-base-en-v1.5"
```

Changing the model name after startup has no effect — the singleton is already loaded. A process restart is required to load a different model.
