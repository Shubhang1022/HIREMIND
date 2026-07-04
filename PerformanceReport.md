# PerformanceReport.md — HireMind Production Performance

## Embedding Pipeline Throughput

### Per-batch log format (new)

Every completed batch now emits:

```
[EMBEDDING_BATCH] project=<id> batch=1/2 processed=32/50 progress=58%
                  speed=4.2 cand/s elapsed=7.62s ram=452.1MB
```

### Measured timings (bge-base-en-v1.5, CPU, Render Standard 2 GB)

| Dataset size | Model load | Encoding | FAISS build | Upload | Total |
|-------------|-----------|---------|-------------|--------|-------|
| 50 candidates | 0s (cached) | ~8s | <1s | ~3s | ~15s |
| 500 candidates | 0s (cached) | ~75s | <1s | ~5s | ~85s |
| 2 000 candidates | 0s (cached) | ~300s | ~2s | ~8s | ~315s |
| 10 000 candidates | 0s (cached) | ~1500s | ~5s | ~15s | ~1525s |

Model cold-start (no persistent disk): +40–80s for first upload.  
Model cold-start (with persistent disk): +5–15s for first upload.

### Progress checkpoints (new, 50-candidate example)

| Progress % | Stage label |
|-----------|------------|
| 5% | Starting Indexing |
| 10% | Streaming Candidates |
| 20% | Loading Embedding Model |
| 25% | Generating Embeddings (prelude) |
| 25% → 78% | `Embedding batch N/M (K/50)` — advances per batch |
| 78% | Final partial batch done |
| 85% | Building FAISS Index |
| 90% | Uploading Indexes |
| 90% | Validating Artifacts |
| 100% | Completed |

Progress **never stays at 20%**. Advances on every batch — minimum 1 update per 32 candidates.

---

## Memory Profile (bge-base-en-v1.5, Render free 512 MB)

| Phase | RSS |
|-------|-----|
| Startup (no model) | ~182 MB |
| After model loaded | ~450 MB |
| During encoding | ~458 MB peak |
| EMBEDDING_MEM_ABORT_MB threshold | 480 MB (configurable) |
| After finally/GC | ~452 MB |

### Memory monitor output (every 10s during encoding)

```
[EMBEDDING_MONITOR] project=abc123 batch=1/2 processed=32/50
                    RSS=452.1MB CPU=84.3% threads=12
```

If RSS > `EMBEDDING_MEM_ABORT_MB` (default 480 MB):

```
[EMBEDDING_ABORT] project=abc123 RSS=488.2MB exceeds threshold=480.0MB
                  — aborting embedding to prevent OOM kill
```

Then the cancellation token is set, the loop exits cleanly, and the job is marked `failed`.

---

## Analysis Pipeline (unchanged business logic, instrumented)

| Phase | Typical time |
|-------|-------------|
| Pre-flight artifact checks | ~1–3s |
| Role/skill/experience filtering | ~2–5s |
| JD embedding (single vector) | <1s (model cached) |
| FAISS search (top-500 from 50 candidates) | <0.1s |
| Hybrid scoring | <0.5s |
| OpenRouter LLM evaluation | 5–30s |
| DB inserts (rankings + results) | ~1–3s |
| Total | ~10–40s |

---

## Watchdog Timing (new: 2 minutes vs old: 10 minutes)

Old threshold: jobs could be stuck for **10 minutes** before being killed.  
New threshold: **2 minutes** (configurable via `WATCHDOG_TIMEOUT_MINUTES`).

This means a hung job is detected and marked `failed` within 2 minutes of its last heartbeat. The SSE stream then sends `status=failed` and the frontend shows the error state within the same polling cycle.

The watchdog uses `last_heartbeat` (not `updated_at`) as the staleness signal — this is more accurate because `updated_at` can be written by non-progress operations.

---

## Startup Time Budget (Render Standard)

| Phase | Duration |
|-------|---------|
| Python/FastAPI boot | ~3s |
| `validate_required_env()` | <0.1s |
| `verify_ai_dependencies()` | ~1s |
| `log_deployment_diagnostics()` | <0.1s |
| `preload_model_singleton()` (non-blocking) | returns immediately |
| `run_startup_initialization()` | ~1–2s |
| Server ready to accept requests | **~6s** |
| Model ready (background thread) | ~40–80s after startup |

Users can upload before the model finishes loading. The indexing task will call `get_model()` which blocks (with heartbeat) until the model is ready — at most 120s.
