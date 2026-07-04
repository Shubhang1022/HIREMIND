-- Migration 002: Ensure user_id column exists on background_jobs
--
-- Context
-- -------
-- The initial schema (001_initial_schema.sql) defines background_jobs with a
-- nullable user_id UUID column.  If the live Supabase instance was provisioned
-- without running that migration (or from an earlier schema version that lacked
-- the column), the backend insert in job_manager.register_job() will fail with:
--
--   PostgrestAPIError: column "user_id" of relation "background_jobs" does not exist
--
-- This migration is fully idempotent.  It adds the column only when absent,
-- re-creates the supporting index, and re-creates the RLS policy.
--
-- How to apply
-- ------------
--   Option A (Supabase Dashboard):
--     1. Open your project in app.supabase.com
--     2. Navigate to SQL Editor
--     3. Paste and run this entire file
--
--   Option B (Supabase CLI):
--     supabase db push
--
-- Safe to re-run: all statements are guarded by IF NOT EXISTS / DO block.

-- Step 1: Add user_id column if it does not exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM   information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = 'background_jobs'
          AND  column_name  = 'user_id'
    ) THEN
        ALTER TABLE public.background_jobs ADD COLUMN user_id UUID;
        RAISE NOTICE 'background_jobs.user_id column added.';
    ELSE
        RAISE NOTICE 'background_jobs.user_id column already exists — no action taken.';
    END IF;
END
$$;

-- Step 2: Ensure the supporting index exists
CREATE INDEX IF NOT EXISTS idx_background_jobs_user_id
    ON public.background_jobs(user_id);

-- Step 3: Re-create the RLS policy (idempotent drop + create)
DROP POLICY IF EXISTS "Users can manage background_jobs for their own projects"
    ON public.background_jobs;

CREATE POLICY "Users can manage background_jobs for their own projects"
    ON public.background_jobs
    FOR ALL
    TO authenticated
    USING      (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- Step 4: Record in migration_history
INSERT INTO public.migration_history (version)
VALUES ('v1.1-background-jobs-user-id')
ON CONFLICT (version) DO NOTHING;
