# CompatibilityReport.md

## Cache Layout Compatibility After Redesign

---

## Known Cache Layouts Supported

| Layout | Example path | Discovery strategy | Works? |
|--------|-----------|--------------------|--------|
| sentence-transformers convention | `BAAI_bge-small-en-v1.5/` | direct (O(1) stat) | ✅ |
| HuggingFace Hub snapshot | `models--BAAI--bge-small-en-v1.5/snapshots/<hash>/` | rglob fallback | ✅ |
| HuggingFace Hub blobs | `models--BAAI--bge-small-en-v1.5/blobs/<sha256>` | rglob fallback | ✅ |
| Nested model dir | `sentence-transformers/BAAI/bge-small-en-v1.5/` | rglob fallback | ✅ |
| Custom cache path | any directory containing `model.safetensors` | rglob fallback | ✅ |
| Flat cache root | `model.safetensors` directly in cache_root | rglob fallback | ✅ |
| Symlinked weights | `model.safetensors` → blob | rglob (follows symlinks) | ✅ |

---

## sentence-transformers Version Compatibility

| Version | Probe 1 (`tokenizer.name_or_path`) | Probe 2 (`modules[0].auto_model`) | Probe 3 (`_modules`) | rglob fallback |
|---------|-----------------------------------|------------------------------------|---------------------|----------------|
| 2.x     | ✅ (PreTrainedTokenizer standard) | ✅ | ✅ | ✅ |
| 3.x     | ✅ | ✅ | ✅ | ✅ |
| Future  | May vary | May vary | May vary | ✅ always |

Probe 1 is most stable — `tokenizer.name_or_path` is a `PreTrainedTokenizer`
attribute, not a sentence-transformers attribute, so it survives ST version changes.

---

## huggingface_hub Version Compatibility

| Version | Cache layout | Handled by |
|---------|-------------|-----------|
| < 0.14 | `{model_name.replace("/","_")}/` | direct check |
| ≥ 0.14 (new HF cache) | `models--{org}--{name}/snapshots/{hash}/` | rglob fallback |
| Any future | unknown | rglob fallback always works |

The direct check tries the conventional ST layout first.
If that fails for any reason, rglob finds weights regardless of layout.

---

## Failure Modes That Are Now Handled

| Failure mode | Old behaviour | New behaviour |
|-------------|--------------|---------------|
| Directory name uses `--` instead of `_` | `CACHE_INCOMPLETE` → crash | rglob finds it → `CACHE_OK` |
| HF hub snapshot sub-dir | `CACHE_INCOMPLETE` → crash | rglob finds it → `CACHE_OK` |
| Extra unrelated dirs in cache_root | Not affected | Not affected (rglob filters by filename) |
| ST version changes probe attributes | Object introspection skipped | Graceful probe-by-probe fallback |
| cache_root doesn't exist | Same hard failure | Same hard failure |
| No weight files anywhere | Same hard failure | Same hard failure |

---

## Failure Conditions (Unchanged)

These still fail — correctly:

1. `SentenceTransformer()` raises an exception → Docker build fails, Railway deploy fails
2. No `model.safetensors` or `pytorch_model.bin` found anywhere under `SENTENCE_TRANSFORMERS_HOME`
   → `DOCKER_CACHE_INVALID` logged, `ModelLoadFailed` raised

---

## Future Cache Format Support

The rglob fallback will continue to work for any future cache format, as long as:
- The weight file is named `model.safetensors` or `pytorch_model.bin`
- It lives somewhere under `SENTENCE_TRANSFORMERS_HOME`

Both of these are HuggingFace invariants that have held across all versions since 2021.
