# EmbeddingAudit.md — Embedding Pipeline Audit

## Model Loading

| Item | Detail |
|------|--------|
| Model | `BAAI/bge-large-en-v1.5` (default, configurable via `settings.embedding_model`) |
| Class | `EmbeddingEncoder` in `src/features/embedding.py` |
| Loading | `_get_encoder()` → lazy-loaded once, cached in `_encoder` module variable |
| Model reload | Triggered if `settings.embedding_model` changes between calls |
| HF cache | Defaults to HuggingFace cache (`~/.cache/huggingface/`), uses `settings.hf_token` if set |
| Failure mode | `ImportError` / `OSError` on model download → `verify_ai_dependencies()` logs but does NOT crash startup |

---

## Embedding Batching

| Item | Detail |
|------|--------|
| Batch size | 32 candidates per batch |
| Text builder | `build_candidate_text(c)` from `src/features/text_builder.py` |
| Disqualified candidates | Assigned empty string `""` → encoded as zero-vector, NOT sent to model |
| Valid text filter | `valid_indices = [i for i, text in enumerate(batch_texts) if text != ""]` |
| Output dtype | `np.float32` |
| Normalization | Done implicitly by `IndexFlatIP` — inner product = cosine after L2 normalization |

---

## Memory Management

| Item | Detail |
|------|--------|
| Raw embeddings | Streamed to disk: `raw_embs_path = Path(temp_dir) / "embeddings.raw"` |
| RAM during encoding | Only 1 batch (32 × dim floats) held in RAM at a time |
| .npy assembly | Built from `.raw` file after all batches complete — O(1) RAM |
| Cleanup | `temp_dir` deleted via `shutil.rmtree()` in `finally` block |
| GC | `gc.collect()` called in `finally` |

---

## HuggingFace Cache

- Default: system HF cache directory
- Custom: `settings.feature_cache_dir` passed to `EmbeddingEncoder`
- Token: `settings.hf_token` (optional) — required for gated models

---

## Embedding Persistence

| File | Bucket | Path |
|------|--------|------|
| NumPy embeddings | `embeddings` | `{project_id}/embeddings_v{N}.npy` |
| Candidate ID list | `embeddings` | `{project_id}/ids_v{N}.json` |
| Raw FAISS index | `faiss-indexes` | `{project_id}/faiss_v{N}.index` |
| Role-specific JSONL | `role-indexes` | `{project_id}/role_{CAT}_v{N}.jsonl` |
| Skill inverted index | `skill-indexes` | `{project_id}/skill_index_v{N}.json` |

---

## FAISS Creation

| Item | Detail |
|------|--------|
| Index type | `faiss.IndexFlatIP` (exact inner product) |
| Dimension | Determined from first valid batch; fallback to `encoder.embedding_dim` |
| Serialization | `faiss.serialize_index(index)` → bytes → `StorageService.upload_file()` |
| Search at analysis time | `index.search(jd_emb, top_k)` with `IDSelectorArray` for pool filtering |

---

## NumPy Output Format

The `.npy` file is hand-assembled (not via `np.save`) to avoid loading all embeddings into RAM:

1. Write NumPy magic bytes: `\x93NUMPY\x01\x00`
2. Write header dict with `shape=(total_candidates, dim)`, dtype `<f4`
3. Pad header to 64-byte alignment
4. Append raw embedding bytes from `.raw` file

**Risk**: If `total_candidates` is miscounted (e.g., due to stream errors), the header shape will mismatch the data. A `np.load()` on such a file will raise a `ValueError`.

---

## Silent Failure Points

| Risk | Status |
|------|--------|
| Model fails to load (missing `sentence-transformers`) | Logged at startup via `verify_ai_dependencies()`; encoding will raise at runtime |
| `faiss` import fails | Now in `requirements.txt` (fix applied); `verify_ai_dependencies()` logs at startup |
| Encoding exception mid-batch | Retried once with `time.sleep(1.0)`, then re-raised → triggers retry loop |
| All candidates are disqualified (zero valid texts) | Handled: zero-vectors are added to FAISS and `.raw`; index will exist but search quality is zero |
| `current_candidate_path` is `None` | Raises `FileNotFoundError("No candidate upload path found in project")` → triggers retry |
| Metadata fallback | Only triggered if RAM > 450MB — NOT a silent failure; caller receives `metadata_only_fallback: true` flag |
