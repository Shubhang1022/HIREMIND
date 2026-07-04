# SupabaseAudit.md — Supabase Schema & Configuration Audit

## Tables Verified

| Table | Key Columns | RLS | Used By |
|-------|-------------|-----|---------|
| `projects` | id, user_id, name, status, embedding_status, candidate_count, job_count, version, current_candidate_path, embeddings_path, faiss_index_path | ✅ | All platform endpoints |
| `jobs` | id, project_id, title, description, required_skills, min_experience, openings, shortlist_size | ✅ | Upload, analyze, list_jobs |
| `candidate_uploads` | id, project_id, storage_path, version, candidate_count, status | ✅ | Upload, analyze guard |
| `rankings` | id, project_id, job_id, status, total_candidates, ranked_count, created_at | ✅ | Analysis, analytics, export |
| `ranking_results` | id, ranking_id, candidate_id, rank, ai_score, match_percent, full_result (JSONB) | ✅ | Analysis, analytics, export |
| `analysis_metrics` | id, ranking_id, project_id, total_candidates, timing metrics | ✅ | Analytics |
| `background_jobs` | id, project_id, user_id, job_type, status, current_stage, progress_percentage, retry_count, last_heartbeat, failure_reason | ✅ | JobManager |
| `audit_logs` | id, project_id, action, timestamp | ✅ | Audit service |
| `migration_history` | id, version, applied_at | N/A | Migration tracking |

---

## Storage Buckets Verified

| Bucket | Purpose | Access |
|--------|---------|--------|
| `candidate-files` | Raw and enriched candidate JSONL | Service key (backend only) |
| `candidate-resumes` | Individual resume files (future use) | Service key |
| `embeddings` | NumPy embedding files + ID mapping | Service key |
| `faiss-indexes` | Serialized FAISS index files | Service key |
| `role-indexes` | Role-category split JSONL files | Service key |
| `skill-indexes` | Skill inverted index JSON | Service key |
| `exports` | Generated CSV/XLSX/PDF exports | Service key |
| `audit-reports` | Audit log files | Service key |

---

## RLS Policies

- All tables have RLS enabled
- Service role key (`SUPABASE_SERVICE_KEY`) bypasses RLS entirely — used in all backend queries
- Frontend uses Supabase anon key + user JWT for direct Supabase calls (auth only — no direct table access from frontend)

---

## Configuration Issues Found

### Issue 1 — SUPABASE_JWT_SECRET set to wrong value

**File**: `backend/.env`  
```
SUPABASE_JWT_SECRET=sb_secret_FDTVjRiSs3kuGwlKoWtctQ_CFBm_MBV
```

This is set to the **service key**, not the JWT secret. The JWT secret is a different value found in:  
`Supabase Dashboard → Settings → API → JWT Settings → JWT Secret`

**Impact in production**: `core/auth.py` attempts `jwt.decode(token, secret, algorithms=["HS256"])`. With the wrong secret, ALL authenticated requests return 401. The system currently falls back to `verify_signature=False` (development mode) because the decode fails, meaning auth is effectively bypassed.

**Fix needed**: Set `SUPABASE_JWT_SECRET` to the actual JWT secret from Supabase dashboard. This is a Render environment variable change — no code change required.

**Note**: This is documented only — not auto-fixed since it requires your specific Supabase project's JWT secret.

---

## Indexes Recommended (Not Schema Changes)

The following DB-side improvements would help but are NOT required for correctness:

| Table | Index | Benefit |
|-------|-------|---------|
| `background_jobs` | `(project_id, status)` | Faster job recovery and watchdog queries |
| `ranking_results` | `(ranking_id, rank)` | Faster ranked result retrieval |
| `rankings` | `(project_id, job_id, created_at DESC)` | Faster latest ranking lookup |

---

## Schema Mismatch Check

No schema mismatches detected between code and expected Supabase schema. The `create_supabase_schema.sql` file in `backend/app/schemas/` aligns with all `supabase_client.table(...)` calls observed in the codebase.

---

## Supabase Service Key Format Note

The project uses a non-standard service key format (`sb_secret_...`). The `create_supabase_client()` function in `storage_provider.py` patches `re.match` to bypass JWT format validation for this key format. This is intentional and works correctly.
