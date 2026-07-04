# FAISSAudit.md

## FAISS Import

```python
import faiss
```

Called inside `process_project_data_task` at the start of the embedding stage. This is a local import (not module-level), so a missing `faiss-cpu` package causes a `ModuleNotFoundError` that is now caught and logged with a specific message:

```
[STAGE_FAIL] project=<id> stage=generate_embeddings faiss_import_error=No module named 'faiss'
             — faiss-cpu may not be installed
```

## FAISS Index Type

`faiss.IndexFlatIP` — exact inner product search. Works correctly with L2-normalized embeddings (BGE v1.5 outputs normalized vectors, so inner product = cosine similarity).

## Index Creation Guards

```python
if index is None:
    index = faiss.IndexFlatIP(dim)
```

`index` is initialized on the first valid (non-empty) batch. If ALL candidates are disqualified (all `is_disqualified=True`), all batches use zero-vectors. `index` is still created and populated with zero-vectors. The FAISS index will exist but searches will return meaningless results — not a crash.

## Null Index Guard (Post-Fix)

Before serialization:

```python
if index is None:
    raise RuntimeError("FAISS index is None — no candidates were encoded.")
```

This prevents `faiss.serialize_index(None)` from raising an unattributed `AttributeError`.

## FAISS Serialization

```python
faiss_content = faiss.serialize_index(index)
```

Returns `bytes`. For 50 candidates at dim=1024: ~204 KB. Uploaded to `faiss-indexes/{project_id}/faiss_v{N}.index`.

## FAISS at Analysis Time

```python
# From run_analysis()
faiss_bytes = StorageService.download_file("faiss-indexes", faiss_key)
index = faiss.deserialize_index(np.frombuffer(faiss_bytes, dtype=np.uint8))
```

The index is loaded fresh for each analysis call — not cached in RAM between analyses (to avoid holding 800 MB + FAISS in RAM simultaneously).

## Stage Log Output

```
[STAGE_START]  project=<id> stage=build_faiss index_ntotal=50 ram=1025.3MB
[STAGE_END]    project=<id> stage=build_faiss elapsed=0.01s serialized_size=204.8KB ntotal=50 ram=1025.4MB
```

The `ntotal` field confirms how many vectors are in the index. If `ntotal=0` after encoding, all candidates were disqualified.

## Failure Modes

| Failure | Log tag | Cause |
|---------|---------|-------|
| `ModuleNotFoundError: faiss` | `[STAGE_FAIL] stage=generate_embeddings faiss_import_error` | `faiss-cpu` not installed |
| `RuntimeError: FAISS index is None` | `[STAGE_FAIL] stage=build_faiss` | All candidates disqualified AND dim lookup via `encoder.embedding_dim` also failed |
| Upload failure | `[STAGE_FAIL] stage=upload_artifacts` | Network/Supabase storage error |
| Artifact validation failure | `[STAGE_FAIL] stage=validate_artifacts` | Upload appeared to succeed but file is not queryable yet (Supabase eventual consistency) |
