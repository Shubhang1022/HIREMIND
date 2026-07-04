# StorageAudit.md — Storage Layer Audit

## Storage Provider Architecture

Two providers selectable via `settings.use_supabase_storage`:

| Provider | Active When | Class |
|----------|-------------|-------|
| `LocalStorageProvider` | `USE_SUPABASE_STORAGE=false` (default) | Writes to `data/` directory |
| `SupabaseStorageProvider` | `USE_SUPABASE_STORAGE=true` | Uses Supabase Storage REST API |

Both implement the same interface: `upload_file`, `download_file`, `download_stream`, `delete_file`, `file_exists`, `generate_signed_url`, `stream_jsonl`.

---

## Expected Files Per Project

For project version `N`:

| File | Bucket | Path | Created By |
|------|--------|------|-----------|
| Candidate JSONL (enriched) | `candidate-files` | `{project_id}/candidate_v{N}.jsonl` | Upload handler (raw), then overwritten by background worker (enriched) |
| NumPy embeddings | `embeddings` | `{project_id}/embeddings_v{N}.npy` | Background worker |
| Candidate ID list | `embeddings` | `{project_id}/ids_v{N}.json` | Background worker |
| FAISS index | `faiss-indexes` | `{project_id}/faiss_v{N}.index` | Background worker |
| Skill inverted index | `skill-indexes` | `{project_id}/skill_index_v{N}.json` | Background worker |
| Role JSONL per category | `role-indexes` | `{project_id}/role_{CAT}_v{N}.jsonl` | Background worker (one per detected role category) |

---

## File Existence Validation

### During indexing (after upload)
Background worker validates all required artifacts exist before marking job `completed`:
```python
required_artifacts = [
    ("embeddings", f"{project_id}/embeddings_v{version}.npy"),
    ("faiss-indexes", f"{project_id}/faiss_v{version}.index"),
    ("embeddings", f"{project_id}/ids_v{version}.json"),
    ("skill-indexes", f"{project_id}/skill_index_v{version}.json"),
    # + role-indexes for each category
]
```
If any are missing → `FileNotFoundError` → retry loop.

### Before analysis
`run_analysis()` pre-flight checks all artifacts:
```python
required_preflights = [
    ("embeddings", ids_key, "Candidate ID Mapping file"),
    ("embeddings", embeddings_key, "Numpy Embeddings file"),
    ("faiss-indexes", faiss_key, "FAISS index file"),
    ("skill-indexes", skill_key, "Skill Index mapping file"),
    # + role-indexes for allowed categories
]
```
If any missing → 409 with specific message telling user to re-upload.

---

## Fixed Storage Bug

### Supabase Storage Stream URL

**Pre-fix** (`SupabaseStorageProvider.download_stream`):
```python
url = f"{self.url}/storage/v1/object/authenticated/{bucket_id}/{path}"
```

**Post-fix**:
```python
url = f"{self.url}/storage/v1/object/{bucket_id}/{path}"
```

**Why it matters**: The `/authenticated/` path in Supabase Storage is designed for **anon/user JWT** (Row Level Security checked). When authenticating with the **service role key**, Supabase expects the standard `/object/` path (service key bypasses RLS). Using `/authenticated/` with the service key causes 400/401 responses for ALL file downloads.

**Impact**: Every candidate stream, embedding load, and FAISS load used this code path. All would have returned empty or thrown `FileNotFoundError` in Supabase storage mode.

---

## Storage Upload Pattern

```python
# Upload with upsert=true (handles re-uploads)
res = self.client.storage.from_(bucket_id).upload(
    path=path, file=content,
    file_options={"cache-control": "3600", "upsert": "true"}
)
```

If upload fails (e.g., file already exists without upsert), falls back to `.update()`. This correctly handles re-uploads without duplicate errors.

---

## Missing Files — Root Causes

| File | Why It Might Be Missing |
|------|------------------------|
| `embeddings_v{N}.npy` | Background job failed before this upload stage; `faiss-cpu` not installed (fixed) |
| `faiss_v{N}.index` | Same as above; FAISS import failed |
| `ids_v{N}.json` | Stream parsing returned 0 candidates; wrong storage URL (fixed) |
| `skill_index_v{N}.json` | Stream parsing returned 0 candidates |
| `role_{CAT}_v{N}.jsonl` | No candidates matched this category; OR stream parsing failed |
| `candidate_v{N}.jsonl` | Upload failed; storage URL bug (fixed) |

---

## Local Storage Path (Development)

When `USE_SUPABASE_STORAGE=false`:  
Base directory: `{project_root}/data/`

Example paths:
- `data/candidate-files/{project_id}/candidate_v1.jsonl`
- `data/embeddings/{project_id}/embeddings_v1.npy`
- `data/faiss-indexes/{project_id}/faiss_v1.index`

Directories are created automatically via `local_file.parent.mkdir(parents=True, exist_ok=True)`.

---

## Export Storage

Export files are generated in-memory and streamed directly — they are NOT stored in Supabase Storage unless the export handler explicitly uploads them. The `exports` bucket exists but is currently unused by the export endpoint (streaming response only).
