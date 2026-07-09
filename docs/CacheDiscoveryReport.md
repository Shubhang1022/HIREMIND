# CacheDiscoveryReport.md

## Summary

Redesigned cache path discovery to use runtime object introspection as the
primary strategy, with recursive filesystem scan as an explicit fallback.

---

## Files Modified

| File | Change |
|------|--------|
| `backend/app/services/model_service.py` | Added `_discover_model_path_from_object()`, `_verify_model_dir()`. Restructured `verify_docker_cache()` with direct+fallback strategy. Added post-construction path discovery in `_do_load()`. |
| `backend/Dockerfile` | Same three-probe discovery logic in the pre-bake verification step. |

---

## Old Strategy

```
verify_docker_cache():
  cache_root.rglob("*")   ← scans ALL files under the cache root
  → finds model.safetensors anywhere in the tree
  → returns parent dir as resolved_model_dir
```

**Problems with old strategy:**
- Always O(n files) even when the conventional directory exists and is valid
- Scan runs BEFORE the model is loaded (no object to introspect)
- No timing, no source attribution
- Single code path — no distinction between fast-path and fallback

---

## New Strategy

### In `verify_docker_cache()` (runs before model construction)

```
PRIMARY:
  Check conventional_dir = cache_root / model_name.replace("/", "_")
  If it exists and contains a weight file → CACHE_OK immediately (O(1) stat)
  [CACHE_DISCOVERY_PERF] strategy=direct elapsed=<ms>

FALLBACK (only if conventional dir missing or empty):
  [MODEL_PATH_DISCOVERY_FAILED] falling_back_to_recursive_scan=True
  cache_root.rglob("*")
  [CACHE_DISCOVERY_PERF] strategy=rglob elapsed=<ms>
```

### In `_do_load()` (runs after model construction)

```
PRIMARY:
  _discover_model_path_from_object(loaded):
    Probe 1: loaded.tokenizer.name_or_path           → public API
    Probe 2: loaded[0].auto_model.config._name_or_path → semi-public
    Probe 3: loaded._modules iteration               → last resort
  [MODEL_PATH_DISCOVERED] resolved_model_dir=... source=<attr>
  [CACHE_DISCOVERY_PERF] strategy=direct_object_introspection elapsed=<ms>
```

---

## Discovery Probe Order

All three probes are attempted in order, stopping on the first success:

| Probe | Attribute | API Status | Works since |
|-------|-----------|-----------|-------------|
| 1 | `model.tokenizer.name_or_path` | Public (`PreTrainedTokenizer`) | ST ≥ 2.0 |
| 2 | `model[0].auto_model.config._name_or_path` | Semi-public | ST ≥ 2.0 |
| 3 | `model._modules[*].auto_model.config._name_or_path` | Private fallback | ST ≥ 1.x |

If all three probes fail, the code logs `[MODEL_PATH_DISCOVERED] source=none` and
the pre-construction `verify_docker_cache()` result stands.

---

## Fail Conditions (unchanged)

Only two hard failures trigger `DOCKER_CACHE_INVALID`:
1. `cache_root` directory does not exist
2. No weight file found anywhere under `cache_root`

Everything else — directory name format, extra folders, symlinks, version-specific
sub-layouts — is accepted as long as the model loaded and weights are present.

---

## Log Output Examples

### Happy path (conventional dir exists)

```
[MODEL_CACHE_VERIFY] ─────────────────────────
  conventional_dir_exists : True
[MODEL_CACHE_VERIFY] CACHE_OK
  strategy           : direct
  resolved_model_dir : /app/.cache/sentence-transformers/BAAI_bge-small-en-v1.5
  weight_files       : ['.../model.safetensors']
  weight_size        : 87.3 MB
  verification_time  : 0.002s
[CACHE_DISCOVERY_PERF] strategy=direct elapsed=0.001s weight_files_found=1
```

### Fallback path (non-standard dir name)

```
[MODEL_PATH_DISCOVERY_FAILED] conventional_dir=.../BAAI_bge-small-en-v1.5
  — falling_back_to_recursive_scan=True
[CACHE_DISCOVERY_PERF] strategy=rglob elapsed=0.043s weight_files_found=1
[MODEL_CACHE_VERIFY] CACHE_OK
  strategy           : rglob
  resolved_model_dir : /app/.cache/sentence-transformers/models--BAAI--bge-small-en-v1.5/...
  verification_time  : 0.045s
```

### Post-construction object discovery

```
[MODEL_PATH_DISCOVERED] resolved_model_dir=/app/.cache/.../BAAI_bge-small-en-v1.5
  source=tokenizer.name_or_path
[CACHE_DISCOVERY_PERF] strategy=direct_object_introspection elapsed=0.0001s
  source=tokenizer.name_or_path
```
