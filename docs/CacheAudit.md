# CacheAudit.md

## Docker Cache File Inventory

**Model**: `BAAI/bge-small-en-v1.5`  
**Cache root**: `/app/.cache/sentence-transformers/`  
**Model directory**: `/app/.cache/sentence-transformers/BAAI_bge-small-en-v1.5/`

---

## Required Files (verified by `verify_docker_cache()` at runtime)

| File | Required? | Checked by code | Notes |
|------|-----------|----------------|-------|
| `config.json` | ✅ Yes | `_REQUIRED_MODEL_FILES` | Transformer architecture config |
| `tokenizer.json` | ✅ Yes | `_REQUIRED_MODEL_FILES` | Fast tokenizer definition |
| `modules.json` | ✅ Yes | `_REQUIRED_MODEL_FILES` | sentence-transformers module list |
| `tokenizer_config.json` | ✅ Yes | `_REQUIRED_MODEL_FILES` | Tokenizer settings |
| `special_tokens_map.json` | ✅ Yes | `_REQUIRED_MODEL_FILES` | Special token mappings |
| `model.safetensors` | ✅ One of these | `_WEIGHT_FILES` | Preferred weights format |
| `pytorch_model.bin` | ✅ One of these | `_WEIGHT_FILES` | Legacy weights format |

---

## Additional Files (present in Docker image, not verified individually)

| File | Purpose |
|------|---------|
| `sentence_bert_config.json` | sentence-transformers wrapper config |
| `vocab.txt` | BERT vocabulary |
| `1_Pooling/config.json` | Pooling layer config (mean pooling) |
| `tokenizer.json` | HuggingFace fast tokenizer |

---

## Verification Logic

`verify_docker_cache()` in `model_service.py`:

```python
# 1. Check SENTENCE_TRANSFORMERS_HOME exists
if not cache_root.is_dir():
    missing.append("cache_root")

# 2. Check model subdirectory exists
# model_dir = cache_root / "BAAI_bge-small-en-v1.5"
if not model_dir.is_dir():
    missing.append("model_dir")

# 3. Check each required file
for fname in _REQUIRED_MODEL_FILES:
    if not (model_dir / fname).is_file():
        missing.append(fname)

# 4. Check at least one weight file
if not any((model_dir / w).is_file() for w in _WEIGHT_FILES):
    missing.append("weights")
```

---

## What Happens on Missing Files

```
[MODEL_CACHE_VERIFY] CACHE_INCOMPLETE
  model_path  : /app/.cache/sentence-transformers/BAAI_bge-small-en-v1.5
  exists      : False
  files       : []
  total_size  : 0.0 MB
  missing     : ['cache_root /app/.cache/sentence-transformers (directory missing)']

[DOCKER_CACHE_INVALID] model=BAAI/bge-small-en-v1.5 ...
```

Followed immediately by `ModelLoadFailed` — **no network attempt, no hang**.

---

## Dockerfile Build Verification

The `RUN python -` step now verifies its own output:

```
[Dockerfile] cache_folder   : /app/.cache/sentence-transformers
[Dockerfile] model_dir      : /app/.cache/sentence-transformers/BAAI_bge-small-en-v1.5
[Dockerfile] model_dir exists: True
[Dockerfile] files          : ['1_Pooling', 'config.json', 'model.safetensors',
                               'modules.json', 'sentence_bert_config.json',
                               'special_tokens_map.json', 'tokenizer.json',
                               'tokenizer_config.json', 'vocab.txt']
[Dockerfile] total_size     : 87.3 MB
[Dockerfile] missing        : []
[Dockerfile] Cache verified OK. 9 files, 87.3 MB.
```

If any file is missing, `sys.exit(1)` aborts the Docker build — **the broken image is never pushed**.

---

## How to Diagnose Cache Issues on Railway

1. Check Railway deploy logs for `[MODEL_CACHE_VERIFY]`
2. If `CACHE_INCOMPLETE` appears: the Docker build step failed silently or the image was not rebuilt
3. If `CACHE_OK` appears but model still fails: check the `[STAGE]` lines for which stage times out
4. Force a clean Docker rebuild: `railway up --detach` after clearing the build cache

---

## Path Resolution

`model_service._resolve_model_dir("BAAI/bge-small-en-v1.5")`:

```python
cache_root  = Path("/app/.cache/sentence-transformers")
sanitized   = "BAAI/bge-small-en-v1.5".replace("/", "_")
           # = "BAAI_bge-small-en-v1.5"
model_dir   = cache_root / sanitized
           # = /app/.cache/sentence-transformers/BAAI_bge-small-en-v1.5
```

This is the **same path** that `SentenceTransformer(cache_folder=st_home)` writes to
during the Dockerfile `RUN python -` step.

If `HF_HOME` or `SENTENCE_TRANSFORMERS_HOME` is overridden in Railway's environment
variables to a different value than `/app/.cache/sentence-transformers`, the cache
verification will immediately print `CACHE_MISS` and fail — which is the correct behaviour.
