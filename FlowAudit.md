# FlowAudit.md — End-to-End Runtime Flow Audit

## 1. Login

| Step | Detail |
|------|--------|
| Function | Supabase Browser Client `supabase.auth.signInWithPassword()` |
| Frontend | `frontend/src/app/(auth)/login/LoginForm.tsx` |
| Backend impact | Issues Supabase JWT (HS256, `authenticated` audience) |
| Stored | Browser session via `@supabase/ssr` |
| Failure points | Wrong `SUPABASE_JWT_SECRET` in backend `.env` → backend JWT verify fails (was set to service key value, not JWT secret) |

---

## 2. Create Project

| Step | Detail |
|------|--------|
| API | `POST /api/v1/platform/projects` |
| Handler | `create_project()` in `platform.py` |
| DB query | `projects.insert({id, user_id, name, ...})` |
| Duplicate guard | Checks `projects` for same user_id + name before insert |
| Returns | Full project object with `embedding_status: "ready"` |
| Failure points | Supabase down; `user_id` fallback to hardcoded UUID if auth bypassed |

---

## 3. Upload Job Description

**Path A — File Upload**

| Step | Detail |
|------|--------|
| API | `POST /api/v1/platform/projects/{id}/upload?upload_type=job_description` |
| Handler | `upload_file()` → `upload_type == "job_description"` branch |
| Processing | DOCX/PDF/TXT parsed → `parse_jd_with_llm()` → fallback to `parse_jd_backup()` |
| DB | `jobs.insert()` + `projects.update({job_count})` |
| Returns | Job object with parsed `required_skills`, `min_experience` |

**Path B — Text/Paste** *(previously broken, now fixed)*

| Step | Detail |
|------|--------|
| API | `POST /api/v1/platform/projects/{id}/jobs` |
| Handler | `create_job()` ← **NEW endpoint added by fix** |
| Processing | Body `description` text → `parse_jd_with_llm()` fallback → `parse_jd_backup()` |
| DB | `jobs.insert()` + `projects.update({job_count})` |
| Failure points (pre-fix) | **MISSING ENDPOINT** — frontend called `POST /jobs`, backend only had `GET /jobs` → 405 Method Not Allowed |

---

## 4. Upload Candidate Dataset

| Step | Detail |
|------|--------|
| API | `POST /api/v1/platform/projects/{id}/upload?upload_type=candidates` |
| Handler | `upload_file()` → `upload_type == "candidates"` branch |
| Parsing | `stream_candidates(file_like, filename)` → supports `.jsonl`, `.json`, `.csv`, binary via `IngestionEngine` |
| Normalization | Each record through `standardize_candidate()` → Redrob nested format |
| Storage | Written to temp file → `StorageService.upload_file("candidate-files", "{project_id}/candidate_v{N}.jsonl")` |
| DB | `candidate_uploads.insert()` + `projects.update({candidate_count, embedding_status: "queued", current_candidate_path})` |
| Job registration | `JobManager.register_job(project_id, user_id, "indexing")` |
| Background trigger | `BackgroundTasks.add_task(process_project_data_task, project_id)` |
| Failure points | Zero records parsed → 422; storage upload timeout; Supabase `candidate_uploads` insert failure |

---

## 5. Create Background Job

| Step | Detail |
|------|--------|
| Function | `JobManager.register_job()` in `services/job_manager.py` |
| DB | `background_jobs.insert({project_id, user_id, status: "queued", ...})` |
| In-memory cache | `_progress_cache[project_id]` populated |
| Cancellation tokens | Cleared for project_id |
| State | `queued` |

---

## 6. Generate Embeddings (Background Worker)

| Step | Detail |
|------|--------|
| Function | `process_project_data_task(project_id)` — runs in thread via `BackgroundTasks` |
| State transition | `queued → processing → embedding` |
| Candidate streaming | `StorageService.stream_jsonl("candidate-files", path)` → `standardize_candidate()` → enrichment |
| Role files | Split by `normalize_role_category()` → written to temp JSONL files |
| Skill index | Inverted index `{skill_name: [candidate_ids]}` |
| Embedding model | `EmbeddingEncoder(model=settings.embedding_model)` — `BAAI/bge-large-en-v1.5` |
| Batch size | 32 candidates per batch |
| Encoding | `encoder.encode_batch(texts)` → `np.float32` array |
| FAISS index | `faiss.IndexFlatIP(dim)` — inner product (cosine after normalization) |
| Failure points | `faiss-cpu` not in requirements (fixed); encoding OOM; model download timeout |

---

## 7. Build FAISS Index

| Step | Detail |
|------|--------|
| State | `embedding → indexing` |
| Embedding persistence | Raw embeddings written to temp `.raw` file → assembled into `.npy` format |
| FAISS serialization | `faiss.serialize_index(index)` → bytes |
| Uploads | `embeddings/{project_id}/embeddings_v{N}.npy`, `faiss-indexes/{project_id}/faiss_v{N}.index`, `embeddings/{project_id}/ids_v{N}.json`, `skill-indexes/{project_id}/skill_index_v{N}.json`, `role-indexes/{project_id}/role_{CAT}_v{N}.jsonl` |
| Artifact validation | `StorageService.file_exists()` checked for all required files |
| Failure points | Missing bucket in Supabase; storage URL bug (fixed: was using `/authenticated/` path) |

---

## 8. Run Analysis

| Step | Detail |
|------|--------|
| API | `POST /api/v1/platform/projects/{id}/analyze` |
| Handler | `run_analysis()` |
| Guard checks | Concurrent lock; `embedding_status` must be `completed/ready`; job exists; artifact pre-flight |
| Role filter | Streams role-JSONL files for COMPATIBLE_CATEGORIES |
| Experience filter | `cand_yoe >= jd_min_exp - 2.0` |
| Skill filter | ≥1 required skill matches |
| Quality heap | Top 2000 by `candidate_quality_score` |
| JD embedding | `encoder.encode_single(jd_text, normalize=True, bge_mode="query")` |
| FAISS search | `IndexFlatIP.search()` with index selector, top 500 |
| Hybrid scoring | `0.70 × cosine_sim + 0.30 × category_boost` |
| LLM evaluation | OpenRouter `google/gemini-2.5-flash`, 60s timeout |
| Fallback | Deterministic scoring if LLM fails/times out |
| Memory guard | Metadata-only fallback if RAM > 450MB |
| Failure points | Analysis endpoint pre-flight rejects if FAISS artifacts missing; deadlock in `_sync_update_progress` (fixed) |

---

## 9. Generate Rankings

| Step | Detail |
|------|--------|
| Engine | `UnifiedRankingEngine.rank_candidates()` in `src/ranking/engine.py` |
| DB inserts | `rankings.insert()` + `ranking_results` batch insert (50 rows/batch) + `analysis_metrics.insert()` |
| Result | Full ranking object returned to frontend |
| Cache | Backend in-memory TTL cache (2 days) + frontend localStorage cache |

---

## 10. Analytics

| Step | Detail |
|------|--------|
| API | `GET /api/v1/platform/projects/{id}/analytics?ranking_id={id}` |
| Data source | `ranking_results` table |
| Computed | Skill distribution, experience bands, match breakdown, hidden gems, risk profiles, hiring funnel |

---

## 11. Export

| Step | Detail |
|------|--------|
| API | `POST /api/v1/platform/projects/{id}/export` |
| Handler | `export_results()` |
| Formats | CSV (stdlib csv), XLSX (openpyxl), PDF (reportlab) |
| Data source | `ranking_results` from Supabase |
| Returns | `StreamingResponse` with appropriate MIME type |
| Failure points | `ranking_id` not found; PDF generation memory; `openpyxl`/`reportlab` missing |
