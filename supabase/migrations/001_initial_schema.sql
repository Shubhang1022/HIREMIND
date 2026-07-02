-- HireMind AI — Production Schema
-- Run via Supabase CLI or SQL Editor

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── User profiles (extends auth.users) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.users (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  full_name TEXT,
  avatar_url TEXT,
  role TEXT NOT NULL DEFAULT 'recruiter' CHECK (role IN ('admin', 'recruiter', 'viewer')),
  company TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Projects ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.projects (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'analyzing', 'completed', 'archived')),
  candidate_count INT NOT NULL DEFAULT 0,
  job_count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_projects_user_id ON public.projects(user_id);

-- ── Uploads ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.uploads (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  file_name TEXT NOT NULL,
  file_type TEXT NOT NULL,
  file_size BIGINT NOT NULL DEFAULT 0,
  storage_path TEXT,
  upload_type TEXT NOT NULL CHECK (upload_type IN ('candidates', 'job_description', 'mixed')),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
  records_parsed INT NOT NULL DEFAULT 0,
  error_message TEXT,
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_uploads_project_id ON public.uploads(project_id);

-- ── Jobs ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.jobs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  company TEXT,
  location TEXT,
  work_mode TEXT,
  description TEXT NOT NULL,
  required_skills JSONB DEFAULT '[]',
  preferred_skills JSONB DEFAULT '[]',
  min_experience FLOAT,
  max_experience FLOAT,
  salary_min INT,
  salary_max INT,
  raw_text TEXT,
  parsed_data JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_jobs_project_id ON public.jobs(project_id);

-- ── Candidates ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.candidates (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
  upload_id UUID REFERENCES public.uploads(id) ON DELETE SET NULL,
  external_id TEXT,
  first_name TEXT,
  last_name TEXT,
  full_name TEXT,
  email TEXT,
  phone TEXT,
  headline TEXT,
  summary TEXT,
  current_title TEXT,
  current_company TEXT,
  location TEXT,
  country TEXT,
  years_of_experience FLOAT,
  raw_data JSONB DEFAULT '{}',
  normalized_data JSONB DEFAULT '{}',
  embedding BYTEA,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_candidates_project_id ON public.candidates(project_id);
CREATE INDEX idx_candidates_external_id ON public.candidates(project_id, external_id);

-- ── Candidate Skills ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.candidate_skills (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  candidate_id UUID NOT NULL REFERENCES public.candidates(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  proficiency TEXT,
  duration_months INT,
  category TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_candidate_skills_candidate_id ON public.candidate_skills(candidate_id);

-- ── Candidate Experience ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.candidate_experience (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  candidate_id UUID NOT NULL REFERENCES public.candidates(id) ON DELETE CASCADE,
  company TEXT,
  title TEXT,
  description TEXT,
  start_date DATE,
  end_date DATE,
  duration_months INT,
  is_current BOOLEAN DEFAULT FALSE,
  location TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_candidate_experience_candidate_id ON public.candidate_experience(candidate_id);

-- ── Candidate Education ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.candidate_education (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  candidate_id UUID NOT NULL REFERENCES public.candidates(id) ON DELETE CASCADE,
  institution TEXT,
  degree TEXT,
  field_of_study TEXT,
  start_year INT,
  end_year INT,
  grade TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_candidate_education_candidate_id ON public.candidate_education(candidate_id);

-- ── Candidate Certifications ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.candidate_certifications (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  candidate_id UUID NOT NULL REFERENCES public.candidates(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  issuing_organization TEXT,
  issue_date DATE,
  expiry_date DATE,
  credential_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_candidate_certifications_candidate_id ON public.candidate_certifications(candidate_id);

-- ── Rankings ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.rankings (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
  job_id UUID NOT NULL REFERENCES public.jobs(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  total_candidates INT NOT NULL DEFAULT 0,
  ranked_count INT NOT NULL DEFAULT 0,
  config JSONB DEFAULT '{}',
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_rankings_project_id ON public.rankings(project_id);
CREATE INDEX idx_rankings_job_id ON public.rankings(job_id);

-- ── Ranking Results ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ranking_results (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  ranking_id UUID NOT NULL REFERENCES public.rankings(id) ON DELETE CASCADE,
  candidate_id UUID NOT NULL REFERENCES public.candidates(id) ON DELETE CASCADE,
  rank INT NOT NULL,
  ai_score FLOAT NOT NULL,
  match_percent FLOAT NOT NULL,
  confidence FLOAT NOT NULL DEFAULT 0.0,
  hiring_readiness TEXT CHECK (hiring_readiness IN ('high', 'medium', 'low', 'not_ready')),
  integrity_score FLOAT NOT NULL DEFAULT 1.0,
  semantic_score FLOAT,
  experience_score FLOAT,
  behavioral_score FLOAT,
  skill_gap_score FLOAT,
  reasoning TEXT,
  strengths JSONB DEFAULT '[]',
  weaknesses JSONB DEFAULT '[]',
  risks JSONB DEFAULT '[]',
  missing_skills JSONB DEFAULT '[]',
  interview_questions JSONB DEFAULT '[]',
  behavioral_signals JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(ranking_id, candidate_id)
);

CREATE INDEX idx_ranking_results_ranking_id ON public.ranking_results(ranking_id);
CREATE INDEX idx_ranking_results_rank ON public.ranking_results(ranking_id, rank);

-- ── Reports ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.reports (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
  ranking_id UUID REFERENCES public.rankings(id) ON DELETE SET NULL,
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  report_type TEXT NOT NULL CHECK (report_type IN ('hiring', 'candidate', 'analytics', 'export')),
  title TEXT NOT NULL,
  format TEXT NOT NULL CHECK (format IN ('csv', 'pdf', 'json')),
  storage_path TEXT,
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reports_project_id ON public.reports(project_id);

-- ── Audit Logs ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.audit_logs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
  project_id UUID REFERENCES public.projects(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  resource_type TEXT,
  resource_id UUID,
  details JSONB DEFAULT '{}',
  ip_address INET,
  user_agent TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_user_id ON public.audit_logs(user_id);
CREATE INDEX idx_audit_logs_project_id ON public.audit_logs(project_id);
CREATE INDEX idx_audit_logs_created_at ON public.audit_logs(created_at DESC);

-- ── Auto-create user profile on signup ───────────────────────────────────────
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.users (id, email, full_name, avatar_url)
  VALUES (
    NEW.id,
    NEW.email,
    COALESCE(NEW.raw_user_meta_data->>'full_name', split_part(NEW.email, '@', 1)),
    NEW.raw_user_meta_data->>'avatar_url'
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ── Updated_at trigger ─────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON public.users
  FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();
CREATE TRIGGER update_projects_updated_at BEFORE UPDATE ON public.projects
  FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();
CREATE TRIGGER update_jobs_updated_at BEFORE UPDATE ON public.jobs
  FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();
CREATE TRIGGER update_candidates_updated_at BEFORE UPDATE ON public.candidates
  FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

-- ── Row Level Security ─────────────────────────────────────────────────────────
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.candidate_skills ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.candidate_experience ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.candidate_education ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.candidate_certifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rankings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ranking_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

-- Users: own profile only
CREATE POLICY users_select_own ON public.users FOR SELECT USING (auth.uid() = id);
CREATE POLICY users_update_own ON public.users FOR UPDATE USING (auth.uid() = id);

-- Projects: owner access
CREATE POLICY projects_all ON public.projects FOR ALL USING (auth.uid() = user_id);

-- Uploads: via project ownership
CREATE POLICY uploads_all ON public.uploads FOR ALL
  USING (EXISTS (SELECT 1 FROM public.projects p WHERE p.id = project_id AND p.user_id = auth.uid()));

-- Jobs: via project ownership
CREATE POLICY jobs_all ON public.jobs FOR ALL
  USING (EXISTS (SELECT 1 FROM public.projects p WHERE p.id = project_id AND p.user_id = auth.uid()));

-- Candidates: via project ownership
CREATE POLICY candidates_all ON public.candidates FOR ALL
  USING (EXISTS (SELECT 1 FROM public.projects p WHERE p.id = project_id AND p.user_id = auth.uid()));

-- Candidate child tables: via candidate → project
CREATE POLICY candidate_skills_all ON public.candidate_skills FOR ALL
  USING (EXISTS (
    SELECT 1 FROM public.candidates c
    JOIN public.projects p ON p.id = c.project_id
    WHERE c.id = candidate_id AND p.user_id = auth.uid()
  ));

CREATE POLICY candidate_experience_all ON public.candidate_experience FOR ALL
  USING (EXISTS (
    SELECT 1 FROM public.candidates c
    JOIN public.projects p ON p.id = c.project_id
    WHERE c.id = candidate_id AND p.user_id = auth.uid()
  ));

CREATE POLICY candidate_education_all ON public.candidate_education FOR ALL
  USING (EXISTS (
    SELECT 1 FROM public.candidates c
    JOIN public.projects p ON p.id = c.project_id
    WHERE c.id = candidate_id AND p.user_id = auth.uid()
  ));

CREATE POLICY candidate_certifications_all ON public.candidate_certifications FOR ALL
  USING (EXISTS (
    SELECT 1 FROM public.candidates c
    JOIN public.projects p ON p.id = c.project_id
    WHERE c.id = candidate_id AND p.user_id = auth.uid()
  ));

-- Rankings: via project ownership
CREATE POLICY rankings_all ON public.rankings FOR ALL
  USING (EXISTS (SELECT 1 FROM public.projects p WHERE p.id = project_id AND p.user_id = auth.uid()));

-- Ranking results: via ranking → project
CREATE POLICY ranking_results_all ON public.ranking_results FOR ALL
  USING (EXISTS (
    SELECT 1 FROM public.rankings r
    JOIN public.projects p ON p.id = r.project_id
    WHERE r.id = ranking_id AND p.user_id = auth.uid()
  ));

-- Reports: via project ownership
CREATE POLICY reports_all ON public.reports FOR ALL
  USING (EXISTS (SELECT 1 FROM public.projects p WHERE p.id = project_id AND p.user_id = auth.uid()));

-- Audit logs: own logs only
CREATE POLICY audit_logs_select ON public.audit_logs FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY audit_logs_insert ON public.audit_logs FOR INSERT WITH CHECK (auth.uid() = user_id);

-- Storage bucket for uploads
INSERT INTO storage.buckets (id, name, public) VALUES ('uploads', 'uploads', false)
ON CONFLICT (id) DO NOTHING;

CREATE POLICY storage_uploads_select ON storage.objects FOR SELECT
  USING (bucket_id = 'uploads' AND auth.uid()::text = (storage.foldername(name))[1]);
CREATE POLICY storage_uploads_insert ON storage.objects FOR INSERT
  WITH CHECK (bucket_id = 'uploads' AND auth.uid()::text = (storage.foldername(name))[1]);
CREATE POLICY storage_uploads_delete ON storage.objects FOR DELETE
  USING (bucket_id = 'uploads' AND auth.uid()::text = (storage.foldername(name))[1]);
