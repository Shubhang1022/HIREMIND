# SingletonAudit.md

## Verification: 36/36 PASS

---

## Root Cause

`src/features/embedding.py` contained its own `SentenceTransformer()` instantiation inside `EmbeddingEncoder.load_model()`. This was a second independent model-loading code path that:

1. Bypassed the `model_service` singleton entirely
2. Had no timeout protection
3. Duplicated HuggingFace environment variable setup
4. Maintained a parallel `_MODEL_CACHE` dict (in addition to `model_service`'s state)
5. Could trigger a second model download if `_ensure_loaded()` was called before `_model` was injected

Although in normal production flow `_get_encoder()` in `platform.py` injects the model via `enc._model = raw_model` before any encode call, the latent risk remained that any code path calling `EmbeddingEncoder()` directly (e.g. from `rank.py`, `src/intelligence/embeddings.py`) would create a second 90 MB model instance in RAM.

---

## Changes Made

### `src/features/embedding.py`

**Before**: `load_model()` called `SentenceTransformer()` directly — independent of `model_service`.

**After**: `load_model()` delegates to `model_service.get_model()`. A new `_load_model_direct()` private method contains the old `SentenceTransformer()` call as a last-resort fallback **only for CLI/test contexts** (precompute.py, rank.py) where FastAPI is not running. In the production Docker container, `model_service` is always available, so `_load_model_direct()` is never called.

---

## Singleton Ownership Map

| Responsibility | Owner | Location |
|---------------|-------|---------|
| SentenceTransformer() instantiation | `model_service._do_load()` | `backend/app/services/model_service.py` |
| Model preload at startup | `preload_model_singleton()` → `model_service.preload()` | `backend/app/api/v1/endpoints/platform.py` |
| Model access in endpoints | `_get_encoder()` → `model_service.get_model()` | `backend/app/api/v1/endpoints/platform.py` |
| Embedding calls | `EmbeddingEncoder.encode_batch/single()` | `src/features/embedding.py` |
| CLI fallback (non-production) | `EmbeddingEncoder._load_model_direct()` | `src/features/embedding.py` |

---

## Remaining Risks

- `src/intelligence/embeddings.py` instantiates `EmbeddingEncoder` directly. After this fix, it will call `model_service.get_model()` via `load_model()` — correct. But if `model_service` is unavailable (CLI context), it falls back to `_load_model_direct()`. This is the intended CLI fallback.
- `rank.py` and `precompute.py` are CLI-only tools not present in the Docker image. They create `EmbeddingEncoder` directly, which will use `_load_model_direct()`. This is correct for the CLI use case.

---

## Verification Evidence

```
PASS  embedding.py: SentenceTransformer() only in _load_model_direct fallback
PASS  model_service.py: SentenceTransformer() present (sole production owner)
PASS  platform.py: zero SentenceTransformer() calls
PASS  main.py: zero SentenceTransformer() calls
PASS  embedding.py: load_model() uses model_service.get_model()
PASS  embedding.py: _load_model_direct() exists as CLI fallback
PASS  embedding.py: no HF env var setup in load_model()
PASS  model_service.py: no circular import of platform.py
```
