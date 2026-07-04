# EmbeddingExecutionTimeline.md

## Expected Timeline (50 candidates, BAAI/bge-large-en-v1.5 already cached)

| Stage | Expected Time | RAM Delta |
|-------|--------------|-----------|
| Load model (cached) | < 5s | +800 MB (model stays in RAM) |
| Load model (cold — first download) | 120–300s | +800 MB |
| Build candidate texts (50 candidates) | < 1s | negligible |
| Encode batch 1 (32 candidates) | 5–15s CPU | negligible |
| Encode batch 2 (18 candidates) | 3–8s CPU | negligible |
| Build FAISS index | < 1s | < 1 MB |
| Write .npy (50 × 1024 dim) | < 1s | < 1 MB |
| Upload .npy to Supabase | 1–5s | negligible |
| Upload FAISS | < 1s | negligible |

## Instrumentation Log Format (Post-Fix)

Each stage now produces structured logs:

```
[STAGE_START]    project=<id> stage=load_model model=BAAI/bge-large-en-v1.5 ram=210.3MB
[STAGE_END]      project=<id> stage=load_model elapsed=4.21s ram=1024.8MB dim=1024
[STAGE_START]    project=<id> stage=generate_embeddings total_candidates=50 batch_size=32 ram=1024.8MB
[STAGE_PROGRESS] project=<id> stage=generate_embeddings first_batch_done dim=1024
[STAGE_END]      project=<id> stage=generate_embeddings elapsed=18.3s ram=1025.2MB processed=50 batches=2 dim=1024
[STAGE_START]    project=<id> stage=write_npy total_candidates=50 dim=1024 ram=1025.2MB
[STAGE_END]      project=<id> stage=write_npy elapsed=0.04s size=0.20MB ram=1025.3MB
[STAGE_START]    project=<id> stage=build_faiss index_ntotal=50 ram=1025.3MB
[STAGE_END]      project=<id> stage=build_faiss elapsed=0.01s serialized_size=204.8KB ntotal=50 ram=1025.4MB
[STAGE_START]    project=<id> stage=upload_artifacts ram=1025.4MB
[STAGE_PROGRESS] project=<id> stage=upload_artifacts uploaded enriched_candidates
[STAGE_PROGRESS] project=<id> stage=upload_artifacts uploaded embeddings_v1.npy size=0.20MB
[STAGE_PROGRESS] project=<id> stage=upload_artifacts uploaded faiss_v1.index size=204.8KB
[STAGE_END]      project=<id> stage=upload_artifacts elapsed=3.2s ram=1025.5MB
[STAGE_START]    project=<id> stage=validate_artifacts
[STAGE_END]      project=<id> stage=validate_artifacts elapsed=1.1s all_present=True
[STAGE_START]    project=<id> stage=mark_completed ram=1025.5MB
[STAGE_PROGRESS] project=<id> stage=mark_completed projects_table_updated
[STAGE_END]      project=<id> stage=mark_completed elapsed=0.3s
[BACKGROUND_TASK_SUCCESS] Project ID=<id> Elapsed=28.4s Memory=1025.5MB
```

## Failure Timeline (cold model download hang)

```
[STAGE_START]    project=<id> stage=load_model model=BAAI/bge-large-en-v1.5 ram=210.3MB
<... 60–300s of silence ...>
[STAGE_FAIL]     project=<id> stage=load_model elapsed=187.3s ram=210.4MB
                 error=ConnectionTimeout(...)
                 Traceback (most recent call last):
                   File ".../sentence_transformers/SentenceTransformer.py", line 89, ...
                   ...
[BACKGROUND_TASK_FAIL] project=<id> attempt=1/3 elapsed=187.5s ...
[PIPELINE_TIMEOUT]     project=<id> stage=load_model elapsed=187.3s  ← if > 60s
[BACKGROUND_TASK_RETRY] project=<id> sleeping=2.0s before attempt 2
...
[BACKGROUND_TASK_FINAL_FAIL] project=<id> marking failed in DB
```

## Timeout Detection

If any stage exceeds 60 seconds:

```
[PIPELINE_TIMEOUT] project=<id> stage=<name> elapsed=<N>s ram=<M>MB
```

This is logged as a WARNING and does NOT abort the stage — it only provides visibility. The retry loop handles eventual failure.

## Model Caching

The embedding model is cached in `_encoder` (module-level global). Once loaded:
- Subsequent uploads in the same process reuse the cached model
- `_get_encoder()` returns immediately without downloading
- A `[STAGE_END] stage=load_model elapsed=<N>s` line with `elapsed < 1.0s` confirms cache hit
