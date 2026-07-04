# BackgroundJobsSchemaAudit.md

## Files Examined

| File | Role |
|------|------|
| `backend/app/services/job_manager.py` | Runtime insert and update logic |
| `backend/app/api/v1/endpoints/platform.py` | Call site — `register_job(project_id, user_id, "indexing")` |
| `backend/app/schemas/create_supabase_schema.sql` | App-level schema reference (applied manually) |
| `supabase/migrations/001_initial_schema.sql` | Version-controlled migration (the authoritative schema source) |
| `backend/scripts/migrate_to_supabase.py` | Data migration script (lock file: `migration.lock` → `"migrated"`) |
| `backend/migration.lock` | Contains `"migrated"` — migration script ran once |

---

## Column Definition: `user_id` in `background_jobs`

### From `001_initial_schema.sql` (migration — authoritative)

```sql
CREATE TABLE IF NOT EXISTS public.background_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
    user_id UUID,                    -- ← NULLABLE, no NOT NULL constraint
    job_type VARCHAR(50) NOT NULL,
    current_stage VARCHAR(100),
    progress_percentage INTEGER DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    last_heartbeat TIMESTAMPTZ DEFAULT now(),
    retry_count INTEGER DEFAULT 0,
    status VARCHAR(50) DEFAULT 'queued',
    failure_reason TEXT
);
```

### From `create_supabase_schema.sql` (app-level reference)

**Identical definition** — same nullable `user_id UUID` with no `NOT NULL`.

### Verdict: The two schema files are **in exact agreement**.

`user_id` is defined as `UUID` with **no `NOT NULL` constraint** in both sources. It is intentionally nullable at the column level.

---

## RLS Policy on `background_jobs`

Both schema files define:

```sql
CREATE POLICY "Users can manage background_jobs for their own projects"
    ON public.background_jobs
    FOR ALL
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);
```

### Analysis of the RLS policy

| Scenario | RLS outcome |
|----------|-------------|
| Insert from **backend** (service role key) | RLS is **bypassed entirely** — service key has `BYPASSRLS` privilege. Insert succeeds regardless of `user_id` value. |
| Insert from **frontend** (user JWT) | `WITH CHECK (auth.uid() = user_id)` — if `user_id` is `NULL`, the expression evaluates to `NULL` (not TRUE), so PostgreSQL rejects the insert with `NEW ROW VIOLATES ROW-LEVEL SECURITY POLICY`. |
| Read from **backend** (service role key) | RLS bypassed — all rows visible. |
| Read from **frontend** (user JWT) | `USING (auth.uid() = user_id)` — rows where `user_id` is `NULL` are invisible. |

**Conclusion**: The backend inserts via service role key — RLS is irrelevant for backend inserts. `user_id` in the insert payload is safe.

---

## Backend Insert Payload (`job_manager.py`)

```python
job_data = {
    "project_id": project_id,
    "user_id": user_id,        # ← passed in from upload handler
    "job_type": job_type,
    "current_stage": "Enqueued",
    "progress_percentage": 0,
    "started_at": now_str,
    "updated_at": now_str,
    "last_heartbeat": now_str,
    "retry_count": 0,
    "status": "queued",
    "failure_reason": None
}
```

**All columns in this payload exist in both schema files.** No column name mismatch.

---

## Call Site (`platform.py` line 1569)

```python
await job_manager.register_job(project_id, user_id, "indexing")
```

`user_id` is sourced from `get_user_id(current_user)`:

```python
def get_user_id(current_user: Optional[AuthUser]) -> str:
    return current_user.id if current_user else "d6c20e10-8518-46b3-ba72-e88e77d2a912"
```

This always returns a **non-null string**. Even when unauthenticated it returns the hardcoded fallback UUID. The value passed to `register_job` is always a valid UUID string — never `None`.

---

## Migration Script Analysis

`migrate_to_supabase.py` **does not touch `background_jobs`** at all — it only migrates `projects`, `jobs`, `rankings`, and `ranking_results`. The `background_jobs` table is populated entirely at runtime by the backend.

The `migration.lock` file containing `"migrated"` records that `migrate_to_supabase.py` completed. This has **no bearing** on whether `001_initial_schema.sql` was executed. The schema SQL must be applied separately (e.g., via the Supabase dashboard SQL editor or `supabase db push`).

---

## Possible Failure Scenario: Schema Not Applied

If `001_initial_schema.sql` was **never run** on the live Supabase instance (i.e., the `background_jobs` table was created by a different, earlier version of the schema), the live table might be missing the `user_id` column entirely.

In that case, the insert in `register_job()` would fail with:
```
PostgrestAPIError: column "user_id" of relation "background_jobs" does not exist
```

This error is **not caught** in `register_job()` — it propagates up to `upload_file()`, which wraps the entire upload logic in a single `try/except` that raises `HTTPException(status_code=422)`. The result: candidate upload returns 422 and the background job is never registered or started.

---

## Summary of Findings

| Item | Finding |
|------|---------|
| `user_id` column defined in `001_initial_schema.sql` | ✅ Yes — `UUID`, nullable |
| `user_id` column defined in `create_supabase_schema.sql` | ✅ Yes — identical definition |
| Both schema files agree | ✅ Yes — exact match |
| Backend always provides a non-null `user_id` value | ✅ Yes — hardcoded fallback ensures this |
| `user_id` required for RLS (backend reads via service key) | ✅ Not required — service key bypasses RLS |
| `user_id` intentionally included | ✅ Yes — supports the RLS policy for future direct frontend queries |
| Migration script applies `background_jobs` table | ❌ No — script does not create or alter this table |
| Error handling for insert failure in `register_job` | ❌ No — exception propagates uncaught to upload handler |
| `register_job` return value checked by caller | ❌ No — `job_id` return value is ignored |

---

## Determination

**`user_id` IS intentionally defined** in both schema files. The column exists in the migration, has an index (`idx_background_jobs_user_id`), and supports the RLS policy. The backend correctly provides it.

**No schema change is needed** based on the migration files.

**The actionable risk** is that if the live Supabase instance was provisioned with an earlier schema that lacked `user_id`, the insert fails silently and breaks the upload→job→embedding chain. The fix plan addresses this with a safe additive migration.
