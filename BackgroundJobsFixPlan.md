# BackgroundJobsFixPlan.md

## Problem Statement

The `upload → background job registration → embedding generation` chain can break silently if:

1. The live Supabase `background_jobs` table lacks the `user_id` column (schema not applied, or applied from an older version).
2. `register_job()` throws on insert but the exception is not caught, propagating as a 422 to the frontend.
3. Even if insert succeeds, the `job_id` return value is ignored by the caller — no way to detect a silent null job_id.

---

## Decision: Keep `user_id` in the Insert

`user_id` is **intentionally defined** in `001_initial_schema.sql`. Both schema files agree. The column supports:
- The index `idx_background_jobs_user_id` (for future user-scoped job queries)
- The RLS policy `USING (auth.uid() = user_id)` (for future direct frontend queries)
- Recovery queries in `recover_interrupted_jobs()` which read `user_id` back

**Do NOT remove `user_id` from the insert.**

---

## Fix 1 — Add `user_id` column to live DB if missing (SQL migration)

If the live Supabase instance was created before or without running `001_initial_schema.sql`, run this:

```sql
-- Safe: adds user_id column only if it doesn't exist
-- Run in Supabase Dashboard → SQL Editor

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'background_jobs'
          AND column_name  = 'user_id'
    ) THEN
        ALTER TABLE public.background_jobs ADD COLUMN user_id UUID;
        RAISE NOTICE 'user_id column added to background_jobs';
    ELSE
        RAISE NOTICE 'user_id column already exists — no action taken';
    END IF;
END
$$;

-- Recreate index (idempotent)
CREATE INDEX IF NOT EXISTS idx_background_jobs_user_id ON public.background_jobs(user_id);

-- Recreate RLS policy (idempotent)
DROP POLICY IF EXISTS "Users can manage background_jobs for their own projects" ON public.background_jobs;
CREATE POLICY "Users can manage background_jobs for their own projects"
    ON public.background_jobs
    FOR ALL
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);
```

**This SQL is safe to run even if `user_id` already exists** — the `IF NOT EXISTS` guard prevents errors.

---

## Fix 2 — Add error handling in `register_job()` (code fix)

Currently `register_job()` has no try/except around the Supabase insert. If the insert fails (column missing, network error, constraint violation), the exception propagates through `upload_file()` and surfaces as a 422 that looks like a parse error.

**Apply**: Wrap the insert in a try/except that logs and re-raises with a clear message.

---

## Fix 3 — Validate `job_id` after registration (code fix)

`register_job()` returns `job_id` but the call site in `platform.py` ignores the return value:
```python
await job_manager.register_job(project_id, user_id, "indexing")
background_tasks.add_task(process_project_data_task, project_id)
```

If `res.data` is empty (insert rejected by Supabase), `job_id` is `None` and the in-memory cache is set with `job_id=None`. The background task still starts but has no persistent job record — it can never update progress in the DB.

**Apply**: Check `job_id` after `register_job()`. If `None`, log a warning but still proceed — the in-memory cache can sustain progress tracking even without a DB record.

---

## Files to Change

| File | Change |
|------|--------|
| `backend/app/services/job_manager.py` | Add try/except around Supabase insert; log on insert failure; return None gracefully |
| `backend/app/api/v1/endpoints/platform.py` | Log warning if `job_id` is None; do not abort background task |
| `supabase/migrations/002_background_jobs_user_id.sql` | Safe additive migration (new file) |

---

## Risk Assessment

| Fix | Risk |
|-----|------|
| SQL migration (additive column) | Zero — `IF NOT EXISTS` guard; nullable column; no data loss |
| Error handling in `register_job` | Zero — adds try/except around existing code; no behavior change on success |
| Null `job_id` handling at call site | Zero — background task always proceeds; only DB tracking is optional |
