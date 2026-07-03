const getApiBase = () => {
  const rawUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  return rawUrl.endsWith('/api/v1') ? rawUrl : `${rawUrl.replace(/\/$/, '')}/api/v1`;
};
const API_BASE = getApiBase();

async function getAuthHeaders(): Promise<HeadersInit> {
  if (typeof window === 'undefined') return {};
  const { createClient } = await import('@/lib/supabase/client');
  const supabase = createClient();
  const { data: { session } } = await supabase.auth.getSession();
  if (session?.access_token) {
    return { Authorization: `Bearer ${session.access_token}` };
  }
  return {};
}

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders,
      ...options.headers,
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const error = new Error(err.detail || `API error ${res.status}`);
    (error as any).status = res.status;
    throw error;
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export interface Project {
  id: string;
  user_id: string;
  name: string;
  description?: string;
  status: string;
  candidate_count: number;
  job_count: number;
  embedding_status?: string;
  started_at?: string;
  last_heartbeat?: string;
  last_updated?: string;
  created_at: string;
  updated_at: string;
}

export interface Job {
  id: string;
  project_id: string;
  title: string;
  company?: string;
  location?: string;
  description: string;
  required_skills: string[];
  created_at: string;
  openings?: number;
  shortlist_size?: number;
  priority?: string;
  min_match_percent?: number;
  salary_range?: string;
  job_location?: string;
  employment_type?: string;
}

export interface RankingResult {
  id: string;
  candidate_id: string;
  candidate_name?: string;
  current_title?: string;
  current_company?: string;
  location?: string;
  years_of_experience?: number;
  top_skills?: string[];
  rank: number;
  ai_score: number;
  match_percent: number;
  confidence: number;
  hiring_readiness: string;
  integrity_score: number;
  reasoning?: string;
  strengths: string[];
  weaknesses: string[];
  risks: string[];
  missing_skills: string[];
  eligibility?: boolean;
  eligibility_reason?: string;
  recommendation_status?: string;
  role_match_percent?: number;
  critical_skill_match_percent?: number;
  experience_match_percent?: number;
  semantic_similarity_percent?: number;
  critical_skill_coverage?: string;
  critical_skill_coverage_percent?: number;
  interview_questions: string[];
}

export interface PrefilterStatistics {
  total_uploaded: number;
  eligible: number;
  filtered_out: number;
  top_categories: string[];
}

export interface Ranking {
  id: string;
  project_id: string;
  job_id: string;
  status: string;
  total_candidates: number;
  ranked_count: number;
  results: RankingResult[];
  created_at: string;
  message?: string;
  alternative_candidates?: RankingResult[];
  prefilter_statistics?: PrefilterStatistics;
  metadata_only_fallback?: boolean;
  ai_enhancement_unavailable?: boolean;
  metrics?: {
    total_candidates: number;
    candidates_filtered: number;
    candidates_retrieved: number;
    candidates_scored: number;
    llm_candidates_evaluated: number;
    retrieval_time: number;
    ranking_time: number;
    llm_time: number;
    total_analysis_time: number;
    
    // V2 timing & count metrics
    filter_time?: number;
    index_lookup_time?: number;
    embedding_time?: number;
    faiss_time?: number;
    scoring_time?: number;
    total_time?: number;
    
    total_candidates_funnel?: number;
    after_role_filter?: number;
    after_experience_filter?: number;
    after_skill_filter?: number;
    faiss_input_count?: number;
    after_faiss?: number;
    after_scoring?: number;
    llm_input_count?: number;
    after_llm_selection?: number;
  };
}

export interface Analytics {
  skill_distribution: { skill: string; count: number }[];
  experience_distribution: { range: string; count: number }[];
  quality_breakdown: Record<string, number>;
  match_breakdown: Record<string, number>;
  hidden_gems: { name: string; rank: number; match: number }[];
  high_risk_profiles: { name: string; rank: number; risks: string[] }[];
  hiring_funnel: Record<string, number>;
}

export interface CandidateRow {
  candidate_id: string;
  name: string;
  current_title: string;
  current_company: string;
  location: string;
  years_of_experience: number;
  top_skills: string[];
  open_to_work: boolean;
  notice_period_days: number | null;
  profile_completeness: number;
}

export interface CandidateListResponse {
  total: number;
  page: number;
  page_size: number;
  pages: number;
  candidates: CandidateRow[];
}

export const platformApi = {
  projects: {
    list: () => apiFetch<Project[]>('/platform/projects'),
    get: (id: string) => apiFetch<Project>(`/platform/projects/${id}`),
    create: (data: { name: string; description?: string; project_hash?: string; dataset_hash?: string; jd_hash?: string }) =>
      apiFetch<Project>('/platform/projects', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: string, data: Partial<{ name: string; description: string; status: string }>) =>
      apiFetch<Project>(`/platform/projects/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
    delete: (id: string) => apiFetch<void>(`/platform/projects/${id}`, { method: 'DELETE' }),
  },
  candidates: {
    list: (projectId: string, params?: { page?: number; pageSize?: number; search?: string }) => {
      const qs = new URLSearchParams();
      if (params?.page) qs.set('page', String(params.page));
      if (params?.pageSize) qs.set('page_size', String(params.pageSize));
      if (params?.search) qs.set('search', params.search);
      return apiFetch<CandidateListResponse>(`/platform/projects/${projectId}/candidates?${qs}`);
    },
    get: (projectId: string, candidateId: string) =>
      apiFetch<any>(`/platform/projects/${projectId}/candidates/${candidateId}`),
  },
  jobs: {
    list: (projectId: string) => apiFetch<Job[]>(`/platform/projects/${projectId}/jobs`),
    create: (projectId: string, data: { title: string; description: string; company?: string; location?: string; required_skills?: string[]; [key: string]: any }) =>
      apiFetch<Job>(`/platform/projects/${projectId}/jobs`, { method: 'POST', body: JSON.stringify(data) }),
  },
  upload: async (
    projectId: string,
    file: File,
    uploadType: 'candidates' | 'job_description' = 'candidates',
    metadata?: Record<string, any>
  ) => {
    const authHeaders = await getAuthHeaders();
    const form = new FormData();
    form.append('file', file);
    const qs = new URLSearchParams();
    qs.set('upload_type', uploadType);
    if (metadata) {
      Object.entries(metadata).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== '') {
          qs.set(k, String(v));
        }
      });
    }
    const res = await fetch(`${API_BASE}/platform/projects/${projectId}/upload?${qs}`, {
      method: 'POST',
      headers: authHeaders,
      body: form,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || 'Upload failed');
    }
    return res.json();
  },
  analyze: (projectId: string, jobId: string, topK = 100, performanceMode = 'balanced') =>
    apiFetch<Ranking>(`/platform/projects/${projectId}/analyze`, {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId, top_k: topK, performance_mode: performanceMode }),
    }),
  ranking: (projectId: string, rankingId: string) =>
    apiFetch<Ranking>(`/platform/projects/${projectId}/rankings/${rankingId}`),
  analytics: (projectId: string, rankingId?: string) =>
    apiFetch<Analytics>(`/platform/projects/${projectId}/analytics${rankingId ? `?ranking_id=${rankingId}` : ''}`),
  export: async (projectId: string, rankingId: string, format: 'csv' | 'json' = 'csv') => {
    const authHeaders = await getAuthHeaders();
    const res = await fetch(`${API_BASE}/platform/projects/${projectId}/export`, {
      method: 'POST',
      headers: { ...authHeaders, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ranking_id: rankingId, format }),
    });
    if (!res.ok) throw new Error('Export failed');
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ranking-${rankingId}.${format}`;
    a.click();
    URL.revokeObjectURL(url);
  },
  cancelIndexing: (projectId: string) =>
    apiFetch<{ status: string; message: string }>(`/platform/projects/${projectId}/cancel-indexing`, {
      method: 'POST',
    }),
  workerStatus: (projectId: string) =>
    apiFetch<any>(`/platform/projects/${projectId}/worker-status`),
  healthStats: () => apiFetch<{
    projects: number;
    candidates: number;
    rankings: number;
    failed_jobs: number;
    duplicate_projects_prevented: number;
    exports_generated: number;
  }>('/platform/health-stats'),
};
