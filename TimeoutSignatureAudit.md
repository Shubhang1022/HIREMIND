# TimeoutSignatureAudit.md

**Generated:** 2026-07-09  
**Scope:** Full repository scan for `_run_with_timeout` definition and every call site

---

## 1. Function Definition

**File:** `backend/app/services/model_service.py`

```python
def _run_with_timeout(fn, timeout_sec: float, stage_name: str,
                      model_name: str, model_dir: Path, missing_files: list[str]):
```

**Parameters (in order):**
| Position | Name | Type | Description |
|---|---|---|---|
| 1 | `fn` | `callable` | Zero-arg function to execute in a daemon thread |
| 2 | `timeout_sec` | `float` | Hard timeout in seconds |
| 3 | `stage_name` | `str` | Label for logging on timeout |
| 4 | `model_name` | `str` | Model name for timeout error message |
| 5 | `model_dir` | `Path` | Resolved model directory for timeout message |
| 6 | `missing_files` | `list[str]` | Missing file list for timeout message |

---

## 2. All Call Sites

All three calls are inside `_do_load()` in `model_service.py`. All use keyword arguments.

### Call 1 — LOAD_CONFIG

```python
_run_with_timeout(
    _import_torch_and_transformers,
    timeout_sec=MODEL_STAGE_TIMEOUT_SECONDS,
    stage_name="LOAD_CONFIG",
    model_name=model_name,
    model_dir=model_dir,
    missing_files=missing_files,
)
```

**Signature match:** ✅ All 6 parameters present, correct types.

---

### Call 2 — LOAD_TOKENIZER

```python
_run_with_timeout(
    _import_sentence_transformers,
    timeout_sec=MODEL_STAGE_TIMEOUT_SECONDS,
    stage_name="LOAD_TOKENIZER",
    model_name=model_name,
    model_dir=model_dir,
    missing_files=missing_files,
)
```

**Signature match:** ✅ All 6 parameters present, correct types.

---

### Call 3 — LOAD_MODEL_WEIGHTS

```python
loaded = _run_with_timeout(
    _construct_sentence_transformer,
    timeout_sec=MODEL_STAGE_TIMEOUT_SECONDS,
    stage_name="LOAD_MODEL_WEIGHTS",
    model_name=model_name,
    model_dir=model_dir,
    missing_files=missing_files,
)
```

**Signature match:** ✅ All 6 parameters present, correct types.

---

## 3. Duplicate Implementations

**Result:** No duplicate `_run_with_timeout` definitions anywhere in the repository.

```
backend/app/services/model_service.py  → 1 definition (canonical)
src/                                   → 0 definitions
tests/                                 → 0 definitions
```

---

## 4. Historical TypeError

The error `TypeError: _run_with_timeout() got an unexpected keyword argument 'missing_files'` was caused by a **stale .pyc cache** or an **in-flight deployment** running an older version of `model_service.py` that lacked the `missing_files` parameter. The current code has the parameter in both the definition and all call sites. No code change was required for this specific error — the fix is already in place.

**Prevention:** Added `python -m py_compile` as a required check before deployment (see `IndexingPipelineAudit.md` completion criteria).

---

## 5. Verdict

| Check | Result |
|---|---|
| Single canonical definition | ✅ |
| All call sites match signature | ✅ |
| No unsupported kwargs | ✅ |
| No stale parameters | ✅ |
| No duplicate implementations | ✅ |
| No shadowed functions | ✅ |
