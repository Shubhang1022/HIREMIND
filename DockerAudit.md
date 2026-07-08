# DockerAudit.md

## Root Cause

The Dockerfile CMD used a hardcoded port (`--port 8000`) instead of `${PORT:-8000}`. Railway injects the `PORT` environment variable at runtime. On Railway, the fixed port caused the health check to fail because Railway was probing the dynamically assigned port, not 8000.

---

## Cache Path Alignment Verification

| Variable | Dockerfile build-time value | model_service._set_hf_cache() runtime value | Match |
|----------|---------------------------|---------------------------------------------|-------|
| `HF_HOME` | `/app/.cache/huggingface` | `/app/.cache/huggingface` | ✅ |
| `TRANSFORMERS_CACHE` | `/app/.cache/huggingface` | `/app/.cache/huggingface` | ✅ |
| `SENTENCE_TRANSFORMERS_HOME` | `/app/.cache/sentence-transformers` | `/app/.cache/sentence-transformers` | ✅ |

The pre-download step during Docker build populates `/app/.cache/sentence-transformers`. At runtime, `model_service._set_hf_cache()` sets the same paths. `SentenceTransformer("BAAI/bge-small-en-v1.5", device="cpu")` will find the cached weights and load from disk without any network request.

---

## Changes Made

### Before

```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

### After

```dockerfile
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --log-level info"]
```

Additions:
- `${PORT:-8000}` — uses Railway's injected `PORT`, falls back to `8000` for local dev
- `--log-level info` — explicit log level for production visibility
- Pre-download step now prints `embedding_dim` for verification

---

## COPY Order (Optimized for Layer Caching)

```dockerfile
# Layer 1: Python dependencies (slow, rarely changes)
COPY backend/requirements.txt .
RUN pip install ...

# Layer 2: HF cache env vars + model download (slow, only re-runs if env changes)
ENV HF_HOME=... SENTENCE_TRANSFORMERS_HOME=...
RUN python - <<'PY'  # pre-downloads model

# Layer 3: Application code (fast, changes frequently)
COPY backend/ /app/
COPY src/ /app/src/
COPY config/ /app/config/
```

App source code changes do NOT trigger a model re-download.

---

## Verification Evidence

```
PASS  Dockerfile: HF_HOME=/app/.cache/huggingface
PASS  Dockerfile: SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers
PASS  model_service: _set_hf_cache uses /app/.cache/huggingface
PASS  model_service: _set_hf_cache uses /app/.cache/sentence-transformers
PASS  Dockerfile CMD uses ${PORT:-8000}
PASS  Dockerfile: --workers 1 (single process = single model instance)
```

---

## Remaining Risks

- If Railway persists the image across restarts (warm restarts), the model is already in `/app/.cache` and loads in ~5s. If Railway rebuilds the image (cold deploy), the model downloads during `docker build` — this is build time, not request time.
- The `/health` endpoint now reports `docker_cache_found: true/false` to verify at runtime whether the pre-download succeeded.
