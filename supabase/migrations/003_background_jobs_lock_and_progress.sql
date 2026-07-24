-- Migration 003: Add lock and progress tracking fields to background_jobs table
DO $$
BEGIN
    -- Add owner_id
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'background_jobs' AND column_name = 'owner_id') THEN
        ALTER TABLE public.background_jobs ADD COLUMN owner_id VARCHAR(100);
    END IF;

    -- Add lease_expires_at
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'background_jobs' AND column_name = 'lease_expires_at') THEN
        ALTER TABLE public.background_jobs ADD COLUMN lease_expires_at TIMESTAMPTZ;
    END IF;

    -- Add processed_candidates
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'background_jobs' AND column_name = 'processed_candidates') THEN
        ALTER TABLE public.background_jobs ADD COLUMN processed_candidates INTEGER DEFAULT 0;
    END IF;

    -- Add total_candidates
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'background_jobs' AND column_name = 'total_candidates') THEN
        ALTER TABLE public.background_jobs ADD COLUMN total_candidates INTEGER DEFAULT 0;
    END IF;

    -- Add ram_usage
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'background_jobs' AND column_name = 'ram_usage') THEN
        ALTER TABLE public.background_jobs ADD COLUMN ram_usage DOUBLE PRECISION DEFAULT 0.0;
    END IF;

    -- Add peak_ram
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'background_jobs' AND column_name = 'peak_ram') THEN
        ALTER TABLE public.background_jobs ADD COLUMN peak_ram DOUBLE PRECISION DEFAULT 0.0;
    END IF;

    -- Add eta
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'background_jobs' AND column_name = 'eta') THEN
        ALTER TABLE public.background_jobs ADD COLUMN eta VARCHAR(50);
    END IF;

    -- Add speed
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'background_jobs' AND column_name = 'speed') THEN
        ALTER TABLE public.background_jobs ADD COLUMN speed DOUBLE PRECISION DEFAULT 0.0;
    END IF;
END $$;

-- Create partial unique index
CREATE UNIQUE INDEX IF NOT EXISTS idx_background_jobs_active_project 
ON public.background_jobs (project_id) 
WHERE status NOT IN ('completed', 'failed', 'cancelled');
