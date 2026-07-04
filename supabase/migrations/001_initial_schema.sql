-- HireMind AI — Idempotent Production Schema
-- Enable standard Supabase extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Projects Table
CREATE TABLE IF NOT EXISTS public.projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'CREATED', -- CREATED, JD_READY, UPLOAD_COMPLETE, INDEXING, INDEX_READY, ANALYZING, COMPLETED, FAILED
    embedding_status VARCHAR(50) DEFAULT 'pending',
    project_hash VARCHAR(255),
    dataset_hash VARCHAR(255),
    jd_hash VARCHAR(255),
    candidate_count INTEGER DEFAULT 0,
    job_count INTEGER DEFAULT 0,
    version INTEGER DEFAULT 1,
    role_index_path TEXT,
    skill_index_path TEXT,
    faiss_index_path TEXT,
    embeddings_path TEXT,
    current_candidate_path TEXT,
    upload_statistics JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 2. Jobs Table
CREATE TABLE IF NOT EXISTS public.jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
    title VARCHAR(512) NOT NULL,
    description TEXT NOT NULL,
    company VARCHAR(255),
    location VARCHAR(255),
    work_mode VARCHAR(50),
    role_category VARCHAR(100),
    seniority VARCHAR(100),
    min_experience NUMERIC,
    required_skills JSONB DEFAULT '[]'::jsonb,
    nice_to_have_skills JSONB DEFAULT '[]'::jsonb,
    preferred_locations JSONB DEFAULT '[]'::jsonb,
    openings INTEGER DEFAULT 5,
    shortlist_size INTEGER DEFAULT 15,
    priority VARCHAR(50) DEFAULT 'balanced',
    min_match_percent NUMERIC,
    salary_range VARCHAR(255),
    job_location VARCHAR(255),
    employment_type VARCHAR(50) DEFAULT 'Full-time',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 3. Candidate Uploads Table
CREATE TABLE IF NOT EXISTS public.candidate_uploads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
    storage_path TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    uploaded_at TIMESTAMPTZ DEFAULT now(),
    candidate_count INTEGER DEFAULT 0,
    status VARCHAR(50) DEFAULT 'PENDING'
);

-- 4. Rankings Table
CREATE TABLE IF NOT EXISTS public.rankings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
    job_id UUID NOT NULL REFERENCES public.jobs(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(50) DEFAULT 'completed',
    total_candidates INTEGER DEFAULT 0,
    ranked_count INTEGER DEFAULT 0,
    dataset_hash VARCHAR(255),
    jd_hash VARCHAR(255),
    version_metadata JSONB DEFAULT '{}'::jsonb,
    metrics JSONB DEFAULT '{}'::jsonb,
    prefilter_statistics JSONB DEFAULT '{}'::jsonb,
    metadata_only_fallback BOOLEAN DEFAULT FALSE,
    ai_enhancement_unavailable BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 5. Ranking Results Table
CREATE TABLE IF NOT EXISTS public.ranking_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ranking_id UUID NOT NULL REFERENCES public.rankings(id) ON DELETE CASCADE,
    candidate_id VARCHAR(100) NOT NULL,
    rank INTEGER NOT NULL,
    score NUMERIC NOT NULL,
    reasoning TEXT,
    eligibility BOOLEAN DEFAULT TRUE,
    critical_skill_coverage VARCHAR(100),
    full_result JSONB NOT NULL
);

-- 6. Analysis Metrics Table
CREATE TABLE IF NOT EXISTS public.analysis_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ranking_id UUID REFERENCES public.rankings(id) ON DELETE CASCADE,
    project_id UUID REFERENCES public.projects(id) ON DELETE CASCADE,
    upload_time NUMERIC DEFAULT 0,
    embedding_time NUMERIC DEFAULT 0,
    faiss_time NUMERIC DEFAULT 0,
    llm_time NUMERIC DEFAULT 0,
    total_analysis_time NUMERIC DEFAULT 0,
    storage_download_time NUMERIC DEFAULT 0,
    storage_upload_time NUMERIC DEFAULT 0,
    openrouter_latency NUMERIC DEFAULT 0,
    average_match_score NUMERIC DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 7. Migration History Table
CREATE TABLE IF NOT EXISTS public.migration_history (
    id SERIAL PRIMARY KEY,
    version VARCHAR(50) NOT NULL UNIQUE,
    migrated_at TIMESTAMPTZ DEFAULT now()
);

-- 8. Background Jobs Table
CREATE TABLE IF NOT EXISTS public.background_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
    user_id UUID,
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

-- 9. Create Performance Indexes
CREATE INDEX IF NOT EXISTS idx_projects_user_id ON public.projects(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_project_id ON public.jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_candidate_uploads_project_id ON public.candidate_uploads(project_id);
CREATE INDEX IF NOT EXISTS idx_rankings_project_id ON public.rankings(project_id);
CREATE INDEX IF NOT EXISTS idx_rankings_job_id ON public.rankings(job_id);
CREATE INDEX IF NOT EXISTS idx_ranking_results_ranking_id ON public.ranking_results(ranking_id);
CREATE INDEX IF NOT EXISTS idx_analysis_metrics_project_id ON public.analysis_metrics(project_id);
CREATE INDEX IF NOT EXISTS idx_background_jobs_project_id ON public.background_jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_background_jobs_user_id ON public.background_jobs(user_id);

-- 10. Enable Row Level Security (RLS)
ALTER TABLE public.projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.candidate_uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rankings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ranking_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analysis_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.background_jobs ENABLE ROW LEVEL SECURITY;

-- 11. Create Security Policies
DROP POLICY IF EXISTS "Users can manage their own projects" ON public.projects;
CREATE POLICY "Users can manage their own projects"
    ON public.projects
    FOR ALL
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can manage jobs for their own projects" ON public.jobs;
CREATE POLICY "Users can manage jobs for their own projects"
    ON public.jobs
    FOR ALL
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.projects
            WHERE public.projects.id = public.jobs.project_id
              AND public.projects.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.projects
            WHERE public.projects.id = public.jobs.project_id
              AND public.projects.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS "Users can manage uploads for their own projects" ON public.candidate_uploads;
CREATE POLICY "Users can manage uploads for their own projects"
    ON public.candidate_uploads
    FOR ALL
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.projects
            WHERE public.projects.id = public.candidate_uploads.project_id
              AND public.projects.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.projects
            WHERE public.projects.id = public.candidate_uploads.project_id
              AND public.projects.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS "Users can manage rankings for their own projects" ON public.rankings;
CREATE POLICY "Users can manage rankings for their own projects"
    ON public.rankings
    FOR ALL
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.projects
            WHERE public.projects.id = public.rankings.project_id
              AND public.projects.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.projects
            WHERE public.projects.id = public.rankings.project_id
              AND public.projects.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS "Users can manage ranking results for their own projects" ON public.ranking_results;
CREATE POLICY "Users can manage ranking results for their own projects"
    ON public.ranking_results
    FOR ALL
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.rankings
            JOIN public.projects ON public.projects.id = public.rankings.project_id
            WHERE public.rankings.id = public.ranking_results.ranking_id
              AND public.projects.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.rankings
            JOIN public.projects ON public.projects.id = public.rankings.project_id
            WHERE public.rankings.id = public.ranking_results.ranking_id
              AND public.projects.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS "Users can manage metrics for their own projects" ON public.analysis_metrics;
CREATE POLICY "Users can manage metrics for their own projects"
    ON public.analysis_metrics
    FOR ALL
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.projects
            WHERE public.projects.id = public.analysis_metrics.project_id
              AND public.projects.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.projects
            WHERE public.projects.id = public.analysis_metrics.project_id
              AND public.projects.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS "Users can manage background_jobs for their own projects" ON public.background_jobs;
CREATE POLICY "Users can manage background_jobs for their own projects"
    ON public.background_jobs
    FOR ALL
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- 12. Create Storage Buckets
INSERT INTO storage.buckets (id, name, public)
VALUES 
    ('candidate-files', 'candidate-files', false),
    ('candidate-resumes', 'candidate-resumes', false),
    ('embeddings', 'embeddings', false),
    ('faiss-indexes', 'faiss-indexes', false),
    ('role-indexes', 'role-indexes', false),
    ('skill-indexes', 'skill-indexes', false),
    ('exports', 'exports', false),
    ('audit-reports', 'audit-reports', false)
ON CONFLICT (id) DO NOTHING;

-- 13. Storage Objects Policies
DROP POLICY IF EXISTS "Users can manage files in their own projects" ON storage.objects;
CREATE POLICY "Users can manage files in their own projects"
    ON storage.objects
    FOR ALL
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.projects
            WHERE public.projects.id::text = split_part(storage.objects.name, '/', 1)
              AND public.projects.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.projects
            WHERE public.projects.id::text = split_part(storage.objects.name, '/', 1)
              AND public.projects.user_id = auth.uid()
        )
    );

-- 14. Audit Logs Table
CREATE TABLE IF NOT EXISTS public.audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    project_id UUID REFERENCES public.projects(id) ON DELETE SET NULL,
    action VARCHAR(255) NOT NULL,
    resource_type VARCHAR(100),
    resource_id VARCHAR(255),
    details JSONB DEFAULT '{}'::jsonb,
    ip_address VARCHAR(45),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Enable RLS for audit_logs
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can manage audit logs for their own projects" ON public.audit_logs;
CREATE POLICY "Users can manage audit logs for their own projects"
    ON public.audit_logs
    FOR ALL
    TO authenticated
    USING (
        project_id IS NULL OR EXISTS (
            SELECT 1 FROM public.projects
            WHERE public.projects.id = public.audit_logs.project_id
              AND public.projects.user_id = auth.uid()
        )
    )
    WITH CHECK (
        project_id IS NULL OR EXISTS (
            SELECT 1 FROM public.projects
            WHERE public.projects.id = public.audit_logs.project_id
              AND public.projects.user_id = auth.uid()
        )
    );

