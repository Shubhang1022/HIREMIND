'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  ArrowLeft, Upload, FileText, Cpu, Loader2, CheckCircle2,
  PenLine, ArrowRight, Sparkles, CheckCircle, AlertTriangle,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { FileUploadZone } from '@/components/upload/FileUploadZone';
import { platformApi, type Project, type Job, type Ranking } from '@/lib/platform-api';
import { toast } from 'sonner';
import { CandidateDetailSheet } from '@/components/candidates/CandidateDetailSheet';

// ── Ranking cache (in-memory, 2-day TTL) ──────────────────────────────────────
const CACHE_TTL_MS = 2 * 24 * 60 * 60 * 1000; // 2 days

interface CacheEntry {
  ranking: Ranking;
  cachedAt: number;
}

const rankingCache = new Map<string, CacheEntry>();

function getCachedRanking(projectId: string, jobId: string): Ranking | null {
  const key = `${projectId}:${jobId}`;
  const entry = rankingCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.cachedAt > CACHE_TTL_MS) {
    rankingCache.delete(key);
    return null;
  }
  return entry.ranking;
}

function setCachedRanking(projectId: string, jobId: string, ranking: Ranking) {
  const key = `${projectId}:${jobId}`;
  rankingCache.set(key, { ranking, cachedAt: Date.now() });
  // Also persist to localStorage for cross-session survival
  try {
    const stored = JSON.parse(localStorage.getItem('hiremind_ranking_cache') || '{}');
    stored[key] = { ranking, cachedAt: Date.now() };
    // Prune expired entries
    Object.keys(stored).forEach(k => {
      if (Date.now() - stored[k].cachedAt > CACHE_TTL_MS) delete stored[k];
    });
    localStorage.setItem('hiremind_ranking_cache', JSON.stringify(stored));
  } catch {}
}

function loadCacheFromStorage() {
  try {
    const stored = JSON.parse(localStorage.getItem('hiremind_ranking_cache') || '{}');
    Object.entries(stored).forEach(([key, val]: [string, any]) => {
      if (Date.now() - val.cachedAt < CACHE_TTL_MS) {
        rankingCache.set(key, val);
      }
    });
  } catch {}
}


export default function ProjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;
  const [project, setProject] = useState<Project | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [ranking, setRanking] = useState<Ranking | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('jobs');

  const has404Ref = useRef(false);

  // Selected JD for analysis
  const [selectedJobId, setSelectedJobId] = useState<string>('');
  const [performanceMode, setPerformanceMode] = useState<string>('balanced');

  // JD text input state
  const [jdInputMode, setJdInputMode] = useState<'file' | 'text'>('file');
  const [jdTitle, setJdTitle] = useState('');
  const [jdText, setJdText] = useState('');
  const [jdSubmitting, setJdSubmitting] = useState(false);

  // Recruiter metadata states (PART 7)
  const [openings, setOpenings] = useState(5);
  const [shortlistSize, setShortlistSize] = useState(15);
  const [priority, setPriority] = useState<'quality' | 'balanced' | 'screening'>('balanced');
  const [minMatchPercent, setMinMatchPercent] = useState<string>('');
  const [salaryRange, setSalaryRange] = useState('');
  const [jobLocation, setJobLocation] = useState('');
  const [employmentType, setEmploymentType] = useState('Full-time');

  // Pagination states
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  // Candidate detail sheet
  const [selectedCandidate, setSelectedCandidate] = useState<{ id: string; name?: string; rankInfo?: any } | null>(null);
  const [workerStatus, setWorkerStatus] = useState<any>(null);
  const [cancelling, setCancelling] = useState(false);

  // Load cache from localStorage on mount
  useEffect(() => { loadCacheFromStorage(); }, []);

  const load = useCallback(async () => {
    if (has404Ref.current) return;
    try {
      const [p, j] = await Promise.all([
        platformApi.projects.get(projectId),
        platformApi.jobs.list(projectId),
      ]);
      setProject(p);
      setJobs(j);
      // Auto-select first job if none selected
      if (j.length > 0 && !selectedJobId) {
        setSelectedJobId(j[0].id);
        // Check cache for this job
        const cached = getCachedRanking(projectId, j[0].id);
        if (cached) setRanking(cached);
      }
    } catch (err: any) {
      if (err.status === 404) {
        has404Ref.current = true;
        toast.error('Project no longer exists.');
        router.push('/projects');
        return;
      }
      toast.error('Failed to load project');
    } finally {
      if (!has404Ref.current) {
        setLoading(false);
      }
    }
  }, [projectId, selectedJobId, router]);

  useEffect(() => { load(); }, [load]);

  // SSE progress listener (Phase 11)
  useEffect(() => {
    const activeStatuses = ['queued', 'processing', 'embedding', 'indexing'];
    if (!project || !activeStatuses.includes(project.embedding_status ?? '')) {
      setWorkerStatus(null);
      return;
    }

    const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
    // Strip any trailing /api/v1 suffix the env var might already contain, then re-append
    const cleanBase = baseUrl.replace(/\/api\/v1\/?$/, '').replace(/\/$/, '');
    const streamUrl = `${cleanBase}/api/v1/platform/projects/${projectId}/progress-stream`;

    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    let reconnectDelay = 2000; // start at 2s, cap at 15s

    const connect = () => {
      if (closed) return;
      es = new EventSource(streamUrl);

      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          setWorkerStatus(data);
          if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
            closed = true;
            es?.close();
            load();
          }
        } catch (err) {
          console.error('[SSE] Error parsing event data:', err);
        }
      };

      es.onerror = () => {
        es?.close();
        es = null;
        if (closed) return;
        // Reconnect with backoff — the worker may still be running
        console.warn(`[SSE] Connection dropped, reconnecting in ${reconnectDelay}ms…`);
        reconnectTimer = setTimeout(() => {
          reconnectDelay = Math.min(reconnectDelay * 1.5, 15000);
          connect();
        }, reconnectDelay);
      };
    };

    connect();

    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
    };
  }, [project?.embedding_status, projectId, load]);

  // Trigger background refresh if project embedding status ready but ranking is fallback (PART 1)
  useEffect(() => {
    const isReady = project && (project.embedding_status === 'ready' || project.embedding_status === 'completed');
    if (ranking && ranking.metadata_only_fallback && isReady && !analyzing) {
      try {
        localStorage.removeItem(`ranking_${projectId}_${selectedJobId}`);
      } catch (e) {}
      toast.info('Project embeddings are ready. Re-running ranking for full semantic match...');
      handleAnalyze(true);
    }
  }, [ranking, project, analyzing, projectId, selectedJobId]);

  const handleSelectJob = (jobId: string) => {
    setSelectedJobId(jobId);
    // Check cache for this job
    const cached = getCachedRanking(projectId, jobId);
    if (cached) {
      setRanking(cached);
      toast.info('Loaded cached ranking for this JD');
    } else {
      setRanking(null); // Clear ranking when switching JD
    }
  };

  const handleCandidateUpload = async (files: File[]) => {
    for (const file of files) {
      await platformApi.upload(projectId, file, 'candidates');
      // Upload accepted — indexing runs in the background.
      // Do NOT show "success" here; the file is queued for indexing, not complete.
      toast.info(`${file.name} accepted — indexing started. Analysis will be available once indexing completes.`);
    }
    // Reload so embedding_status is refreshed and SSE listener activates
    await load();
  };

  const [retrying, setRetrying] = useState(false);

  const handleRetryIndexing = async () => {
    setRetrying(true);
    try {
      const authHeaders = await (async () => {
        if (typeof window === 'undefined') return {};
        const { createClient } = await import('@/lib/supabase/client');
        const supabase = createClient();
        const { data: { session } } = await supabase.auth.getSession();
        if (session?.access_token) return { Authorization: `Bearer ${session.access_token}` };
        return {};
      })();
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      const cleanBase = baseUrl.replace(/\/api\/v1\/?$/, '').replace(/\/$/, '');
      const res = await fetch(`${cleanBase}/api/v1/platform/projects/${projectId}/retry-indexing`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders as HeadersInit },
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error((err.detail as any)?.message || err.detail || 'Retry failed');
      }
      toast.info('Indexing restarted — no re-upload needed. Monitoring progress...');
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to retry indexing');
    } finally {
      setRetrying(false);
    }
  };

  const handleJobUpload = async (files: File[]) => {
    for (const file of files) {
      await platformApi.upload(projectId, file, 'job_description', {
        title: jdTitle.trim() || undefined,
        openings: Number(openings) || 5,
        shortlist_size: Number(shortlistSize) || 15,
        priority,
        min_match_percent: minMatchPercent ? Number(minMatchPercent) : undefined,
        salary_range: salaryRange.trim() || undefined,
        job_location: jobLocation.trim() || undefined,
        employment_type: employmentType.trim() || undefined,
      });
      toast.success('Job description uploaded and parsed by AI');
    }
    setJdText('');
    setJdTitle('');
    await load();
  };

  const handleJdTextSubmit = async () => {
    if (!jdText.trim()) {
      toast.error('Please enter a job description');
      return;
    }
    setJdSubmitting(true);
    try {
      const newJob = await platformApi.jobs.create(projectId, {
        title: jdTitle.trim() || 'Job Description',
        description: jdText.trim(),
        openings: Number(openings) || 5,
        shortlist_size: Number(shortlistSize) || 15,
        priority,
        min_match_percent: minMatchPercent ? Number(minMatchPercent) : undefined,
        salary_range: salaryRange.trim() || undefined,
        job_location: jobLocation.trim() || undefined,
        employment_type: employmentType.trim() || undefined,
      });
      toast.success('Job description saved — AI extracted requirements');
      setJdText('');
      setJdTitle('');
      // Auto-select the newly created job
      setSelectedJobId(newJob.id);
      setRanking(null);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save job description');
    } finally {
      setJdSubmitting(false);
    }
  };

  const handleAnalyze = async (force: boolean = false) => {
    if (!project?.candidate_count) {
      toast.error('Upload candidate data first');
      return;
    }
    if (!selectedJobId) {
      toast.error('Select or upload a job description first');
      setActiveTab('jobs');
      return;
    }

    // Check cache first
    if (!force) {
      const cached = getCachedRanking(projectId, selectedJobId);
      if (cached) {
        setRanking(cached);
        toast.success('Loaded cached ranking — no re-analysis needed');
        router.push(`/ranking?project=${projectId}&ranking=${cached.id}`);
        return;
      }
    }

    setAnalyzing(true);
    const selectedJob = jobs.find(j => j.id === selectedJobId);
    toast.info(`Analyzing ${project.candidate_count} candidates against "${selectedJob?.title || 'JD'}"...`);

    try {
      const result = await platformApi.analyze(projectId, selectedJobId, 100, performanceMode);
      setRanking(result);
      // Cache the result
      setCachedRanking(projectId, selectedJobId, result);
      toast.success(`Analysis complete — ${result.ranked_count} candidates ranked`);
      await load();
      router.push(`/ranking?project=${projectId}&ranking=${result.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Analysis failed');
    } finally {
      setAnalyzing(false);
    }
  };

  if (loading) {
    return <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-indigo-400" /></div>;
  }

  if (!project) {
    return <div className="p-8 text-center text-muted-foreground">Project not found</div>;
  }

  const steps = [
    { label: 'Project Created', done: true, icon: CheckCircle2 },
    { label: 'Job Description Added', done: !!selectedJobId, icon: FileText },
    { label: 'Candidates Uploaded', done: project.candidate_count > 0, icon: Upload },
    { label: 'Analysis Complete', done: !!ranking, icon: Cpu },
  ];

  const isEmbeddingReady = project.embedding_status === 'ready' || project.embedding_status === 'completed';
  const canRunAnalysis = project.candidate_count > 0 && !!selectedJobId && isEmbeddingReady;
  const selectedJob = jobs.find(j => j.id === selectedJobId);

  const formatCount = (num: number): string => {
    if (num >= 1000000) return (num / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    return String(num);
  };

  return (
    <div className="p-6 lg:p-8 space-y-8">
      <div>
        <Link href="/projects" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground mb-4 transition-colors">
          <ArrowLeft className="w-4 h-4" /> Back to Projects
        </Link>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold">{project.name}</h1>
            {project.description && <p className="text-muted-foreground mt-1">{project.description}</p>}
          </div>
          <div className="flex items-center gap-3">
            {project.status === 'completed' && <Badge className="bg-green-500/20 text-green-400 border-green-500/30">Completed</Badge>}
            {project.status === 'processing' && <Badge className="bg-blue-500/20 text-blue-400 border-blue-500/30 animate-pulse">Processing...</Badge>}
            {project.status === 'ranking' && <Badge className="bg-indigo-500/20 text-indigo-400 border-indigo-500/30 animate-pulse">Ranking Candidates...</Badge>}
            {project.status === 'uploaded' && <Badge className="bg-purple-500/20 text-purple-400 border-purple-500/30">Uploaded</Badge>}
            {project.status === 'failed' && <Badge className="bg-red-500/20 text-red-400 border-red-500/30">Failed</Badge>}
            {project.status === 'draft' && <Badge className="bg-muted text-muted-foreground">Draft</Badge>}
            {!['completed', 'processing', 'ranking', 'uploaded', 'failed', 'draft'].includes(project.status) && (
              <Badge variant="secondary" className="w-fit capitalize">{project.status}</Badge>
            )}
            
            <div className="flex items-center gap-2">
              <select
                value={performanceMode}
                onChange={(e) => setPerformanceMode(e.target.value)}
                className="bg-zinc-900 border border-zinc-700/50 rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-indigo-500/50 cursor-pointer"
                disabled={analyzing}
              >
                <option value="fast">⚡ Fast (2-5s)</option>
                <option value="balanced">⚖️ Balanced (5-10s)</option>
                <option value="deep">🔍 Deep (10-30s)</option>
              </select>
              <Button
                onClick={() => handleAnalyze()}
                disabled={analyzing || !canRunAnalysis}
                className={`text-white border-0 shadow-lg transition-all ${
                  canRunAnalysis
                    ? 'bg-gradient-to-r from-indigo-500 to-purple-600 shadow-indigo-500/20 hover:opacity-90'
                    : 'bg-zinc-800 text-zinc-500 cursor-not-allowed shadow-none hover:bg-zinc-800'
                }`}
                title={!canRunAnalysis ? 'Upload candidates and select job description first' : undefined}
              >
                {analyzing
                  ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Analysis Running...</>
                  : <><Sparkles className="w-4 h-4 mr-2" />Run AI Analysis</>}
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* Progress steps */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {steps.map((s, i) => (
          <div key={s.label} className={`p-4 rounded-xl border transition-colors ${
            s.done
              ? 'border-green-500/40 bg-green-500/5'
              : i === 0 || steps[i - 1]?.done
              ? 'border-indigo-500/30 bg-indigo-500/5'
              : 'border-border bg-muted/10'
          }`}>
            <div className="flex items-center gap-3">
              {s.done
                ? <CheckCircle2 className="w-5 h-5 text-green-400 shrink-0" />
                : <s.icon className={`w-5 h-5 shrink-0 ${i === 0 || steps[i - 1]?.done ? 'text-indigo-400' : 'text-muted-foreground'}`} />}
              <div className="min-w-0">
                <p className="text-sm font-medium truncate">{s.label}</p>
                {s.done && i === 1 && selectedJob && (
                  <p className="text-xs text-green-400/80 truncate max-w-[120px]">{selectedJob.title}</p>
                )}
                {s.done && i === 2 && (
                  <p className="text-xs text-green-400/80">{project.candidate_count} candidates</p>
                )}
                {s.done && i === 0 && <p className="text-xs text-green-400/80">Active</p>}
                {s.done && i === 3 && <p className="text-xs text-green-400/80">Complete</p>}
                {!s.done && <p className="text-xs text-muted-foreground">Pending</p>}
              </div>
            </div>
          </div>
        ))}
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid grid-cols-3 w-full max-w-md">
          <TabsTrigger value="jobs">
            Job Description
            {selectedJobId && <CheckCircle2 className="w-3.5 h-3.5 ml-1.5 text-green-400" />}
          </TabsTrigger>
          <TabsTrigger value="candidates">
            Candidates
            {project.candidate_count > 0 && (
              <Badge variant="secondary" className="ml-2 text-xs">{project.candidate_count}</Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="results">Results</TabsTrigger>
        </TabsList>

        {/* ── Job Description Tab ── */}
        <TabsContent value="jobs" className="mt-6">
          <Card>
            <CardHeader>
              <CardTitle>Job Description</CardTitle>
              <CardDescription>Select a saved JD or add a new one. Analysis runs against the selected JD.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">

              {/* Saved JDs — select one */}
              {jobs.length > 0 && (
                <div className="space-y-2">
                  <p className="text-sm font-semibold">Select a Job Description for Analysis</p>
                  <div className="space-y-2">
                    {jobs.map(j => {
                      const isSelected = selectedJobId === j.id;
                      const isBinary = j.description?.startsWith('PK');
                      const preview = isBinary ? '(Parsed by AI — binary file)' : j.description;
                      return (
                        <button
                          key={j.id}
                          onClick={() => handleSelectJob(j.id)}
                          className={`w-full text-left p-4 rounded-xl border transition-all ${
                            isSelected
                              ? 'border-indigo-500/60 bg-indigo-500/10 ring-1 ring-indigo-500/40'
                              : 'border-border/50 bg-muted/10 hover:border-indigo-500/30 hover:bg-muted/20'
                          }`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <p className="font-medium truncate">{j.title}</p>
                                {isSelected && (
                                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 shrink-0">
                                    <CheckCircle className="w-3 h-3" /> Selected
                                  </span>
                                )}
                              </div>
                              {(j as any).required_skills?.length > 0 && (
                                <div className="flex flex-wrap gap-1 mt-1.5">
                                  {(j as any).required_skills.slice(0, 5).map((s: string) => (
                                    <Badge key={s} variant="secondary" className="text-xs truncate max-w-[140px]">{s}</Badge>
                                  ))}
                                </div>
                              )}
                              <p className="text-xs text-muted-foreground mt-2 line-clamp-2 break-words">{preview}</p>
                            </div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Add new JD */}
              <div className="pt-3 border-t border-border/40 space-y-4">
                <p className="text-sm font-semibold text-muted-foreground">Add a New Job Description</p>

                {/* Shared Recruiter Metadata (Always visible) */}
                <div className="space-y-4 border p-4 rounded-xl bg-muted/10 border-border/40">
                  <div className="space-y-2">
                    <Label htmlFor="jd-title">Job Title <span className="text-muted-foreground font-normal">(optional)</span></Label>
                    <Input id="jd-title" placeholder="e.g. Senior AI Engineer" value={jdTitle} onChange={e => setJdTitle(e.target.value)} />
                  </div>
                  
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label htmlFor="jd-openings">Number of Openings</Label>
                      <Input id="jd-openings" type="number" min="1" value={openings} onChange={e => setOpenings(parseInt(e.target.value) || 1)} />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="jd-shortlist">Shortlist Size</Label>
                      <Input id="jd-shortlist" type="number" min="1" value={shortlistSize} onChange={e => setShortlistSize(parseInt(e.target.value) || 1)} />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="jd-priority">Evaluation Priority</Label>
                      <select
                        id="jd-priority"
                        value={priority}
                        onChange={e => setPriority(e.target.value as any)}
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <option value="quality">Highest Quality</option>
                        <option value="balanced">Balanced</option>
                        <option value="screening">Fast Screening</option>
                      </select>
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="jd-min-match">Min Match % <span className="text-muted-foreground font-normal">(optional)</span></Label>
                      <Input id="jd-min-match" type="number" min="0" max="100" placeholder="e.g. 70" value={minMatchPercent} onChange={e => setMinMatchPercent(e.target.value)} />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="jd-salary">Salary Range <span className="text-muted-foreground font-normal">(optional)</span></Label>
                      <Input id="jd-salary" placeholder="e.g. 15-25 LPA" value={salaryRange} onChange={e => setSalaryRange(e.target.value)} />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="jd-location">Job Location <span className="text-muted-foreground font-normal">(optional)</span></Label>
                      <Input id="jd-location" placeholder="e.g. Bengaluru / Hybrid" value={jobLocation} onChange={e => setJobLocation(e.target.value)} />
                    </div>
                    <div className="col-span-2 space-y-2">
                      <Label htmlFor="jd-employment">Employment Type</Label>
                      <select
                        id="jd-employment"
                        value={employmentType}
                        onChange={e => setEmploymentType(e.target.value)}
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <option value="Full-time">Full-time</option>
                        <option value="Part-time">Part-time</option>
                        <option value="Contract">Contract</option>
                        <option value="Internship">Internship</option>
                      </select>
                    </div>
                  </div>
                </div>

                {/* Mode toggle */}
                <div className="space-y-2">
                  <Label>Select JD Input Method</Label>
                  <div className="flex gap-2 p-1 bg-muted rounded-lg w-fit">
                    <button
                      onClick={() => setJdInputMode('file')}
                      className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                        jdInputMode === 'file' ? 'bg-background shadow text-foreground' : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      <Upload className="w-4 h-4" /> Upload File
                    </button>
                    <button
                      onClick={() => setJdInputMode('text')}
                      className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                        jdInputMode === 'text' ? 'bg-background shadow text-foreground' : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      <PenLine className="w-4 h-4" /> Paste / Type
                    </button>
                  </div>
                </div>

                {/* Upload File Mode */}
                {jdInputMode === 'file' && (
                  <div className="space-y-2">
                    <Label>Upload JD Document <span className="text-destructive">*</span></Label>
                    <FileUploadZone onUpload={handleJobUpload} uploadType="job_description" multiple={false} />
                  </div>
                )}

                {jdInputMode === 'text' && (
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <Label htmlFor="jd-text">Job Description <span className="text-destructive">*</span></Label>
                      <Textarea
                        id="jd-text"
                        placeholder={"Paste or type the full job description here..."}
                        value={jdText}
                        onChange={e => setJdText(e.target.value)}
                        className="min-h-[200px] resize-y font-mono text-sm"
                      />
                      <p className="text-xs text-muted-foreground">{jdText.length} characters</p>
                    </div>
                    <Button
                      onClick={handleJdTextSubmit}
                      disabled={jdSubmitting || !jdText.trim()}
                      className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white border-0"
                    >
                      {jdSubmitting
                        ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />AI is parsing JD...</>
                        : <><FileText className="w-4 h-4 mr-2" />Save & Auto-Select</>}
                    </Button>
                  </div>
                )}
              </div>

              {/* Continue to Candidates CTA */}
              {selectedJobId && project.candidate_count === 0 && (
                <div className="pt-4 border-t border-border/40 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <p className="text-sm text-muted-foreground">Job description selected. Next step: upload candidates.</p>
                  <Button onClick={() => setActiveTab('candidates')} className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white border-0">
                    Continue — Upload Candidates <ArrowRight className="w-4 h-4 ml-2" />
                  </Button>
                </div>
              )}

              {/* Run Analysis CTA */}
              {selectedJobId && project.candidate_count > 0 && (
                <div className="pt-3 border-t border-border/40">
                  <div className="flex items-center gap-3 p-3 rounded-xl bg-indigo-500/5 border border-indigo-500/20 mb-3">
                    <CheckCircle2 className="w-4 h-4 text-indigo-400 shrink-0" />
                    <p className="text-sm text-indigo-300">
                      Ready: <span className="font-medium">{project.candidate_count} candidates</span> × <span className="font-medium">"{selectedJob?.title}"</span>
                    </p>
                    {getCachedRanking(projectId, selectedJobId) && (
                      <Badge variant="secondary" className="text-xs ml-auto shrink-0">Cached</Badge>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-3">
                    <select
                      value={performanceMode}
                      onChange={(e) => setPerformanceMode(e.target.value)}
                      className="bg-zinc-900 border border-zinc-700/50 rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-indigo-500/50 cursor-pointer"
                    >
                      <option value="fast">⚡ Fast (2-5s)</option>
                      <option value="balanced">⚖️ Balanced (5-10s)</option>
                      <option value="deep">🔍 Deep (10-30s)</option>
                    </select>
                    <Button
                      onClick={() => handleAnalyze()}
                      disabled={analyzing}
                      size="lg"
                      className="w-full sm:w-auto bg-gradient-to-r from-indigo-500 to-purple-600 text-white border-0 shadow-lg shadow-indigo-500/20"
                    >
                      {analyzing
                        ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Analysis Running...</>
                        : getCachedRanking(projectId, selectedJobId)
                        ? <><Sparkles className="w-4 h-4 mr-2" />View Cached Ranking</>
                        : <><Sparkles className="w-4 h-4 mr-2" />Run AI Analysis — Rank Candidates</>}
                    </Button>
                  </div>
                </div>
              )}

              {/* Warning if no JD selected and candidates exist */}
              {!selectedJobId && project.candidate_count > 0 && jobs.length === 0 && (
                <div className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/20">
                  <p className="text-sm text-amber-400">⚠ Add a job description above before running analysis.</p>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Candidates Tab ── */}
        <TabsContent value="candidates" className="mt-6">
          <Card>
            <CardHeader>
              <CardTitle>Upload Candidate Data</CardTitle>
              <CardDescription>Upload JSONL, JSON array, CSV, or PDF resumes.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <FileUploadZone onUpload={handleCandidateUpload} uploadType="candidates" />

              {/* Indexing in-progress banner */}
              {project.candidate_count > 0 && ['queued', 'processing', 'embedding', 'indexing'].includes(project.embedding_status ?? '') && (
                <div className="p-4 rounded-xl border border-amber-500/25 bg-amber-950/20">
                  <div className="flex items-start gap-3">
                    <Loader2 className="w-5 h-5 text-amber-400 animate-spin shrink-0 mt-0.5" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold text-amber-300">
                        Indexing in progress — {workerStatus?.current_stage || project.embedding_status}
                      </p>
                      <p className="text-xs text-amber-300/70 mt-0.5">
                        Analysis will be enabled automatically once indexing completes.
                      </p>
                      {workerStatus && (
                        <div className="mt-2 space-y-1.5">
                          <div className="w-full bg-zinc-800 rounded-full h-1.5 overflow-hidden">
                            <div
                              className="bg-amber-500 h-1.5 rounded-full transition-all duration-500"
                              style={{ width: `${workerStatus.progress_percentage || 0}%` }}
                            />
                          </div>
                          <p className="text-[10px] text-muted-foreground">
                            {workerStatus.progress_percentage || 0}% — {workerStatus.processed_candidates || 0}/{workerStatus.total_candidates || 0} candidates
                          </p>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {/* Indexing failed banner with retry button */}
              {project.embedding_status === 'failed' && project.candidate_count > 0 && (
                <div className="p-4 rounded-xl border border-red-500/25 bg-red-950/20">
                  <div className="flex items-start gap-3">
                    <AlertTriangle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold text-red-300">Indexing failed</p>
                      <p className="text-xs text-red-300/70 mt-0.5">
                        Your file is still stored. Click Retry Indexing — no re-upload needed.
                      </p>
                      <Button
                        onClick={handleRetryIndexing}
                        disabled={retrying}
                        size="sm"
                        className="mt-3 bg-red-700 hover:bg-red-600 text-white border-0"
                      >
                        {retrying
                          ? <><Loader2 className="w-3.5 h-3.5 mr-2 animate-spin" />Retrying...</>
                          : '↺ Retry Indexing — No Re-upload Needed'}
                      </Button>
                    </div>
                  </div>
                </div>
              )}

              {project.candidate_count > 0 && project.embedding_status === 'completed' && (
                <div className="pt-2">
                  <div className="flex items-center gap-3 p-4 rounded-xl bg-green-500/5 border border-green-500/20 mb-4">
                    <CheckCircle2 className="w-5 h-5 text-green-400 shrink-0" />
                    <div>
                      <p className="text-sm font-medium text-green-400">{project.candidate_count} candidates uploaded</p>
                      <p className="text-xs text-muted-foreground">Ready for analysis</p>
                    </div>
                  </div>
                  <Button onClick={() => setActiveTab('results')} className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white border-0">
                    Continue — Run AI Analysis <ArrowRight className="w-4 h-4 ml-2" />
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Results Tab ── */}
        <TabsContent value="results" className="mt-6 space-y-6">
          {!ranking ? (
            <Card>
              <CardContent className="py-12 text-center max-w-xl mx-auto space-y-6">
                <Sparkles className="w-12 h-12 mx-auto text-indigo-400" />
                <div>
                  <h3 className="text-xl font-bold text-foreground">AI Retrieval & Compatibility Analysis</h3>
                  <p className="text-sm text-muted-foreground mt-2">
                    Rank candidates using our JD-First analysis pipeline. Candidates are dynamically filtered, semantically matched, and reviewed via Gemini AI.
                  </p>
                </div>

                <div className="bg-zinc-900/50 p-4 rounded-xl border border-zinc-800 space-y-3 text-left">
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Analysis Requirements</p>
                  <div className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-2">
                      {project.candidate_count > 0 ? (
                        <CheckCircle2 className="w-4 h-4 text-green-400 shrink-0" />
                      ) : (
                        <div className="w-4 h-4 rounded-full border border-zinc-600 shrink-0" />
                      )}
                      1. Candidate Dataset Uploaded
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {project.candidate_count > 0 ? `${project.candidate_count} candidates` : 'Missing'}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-2">
                      {selectedJobId ? (
                        <CheckCircle2 className="w-4 h-4 text-green-400 shrink-0" />
                      ) : (
                        <div className="w-4 h-4 rounded-full border border-zinc-600 shrink-0" />
                      )}
                      2. Job Description Added/Selected
                    </span>
                    <span className="text-xs text-muted-foreground max-w-[180px] truncate">
                      {selectedJobId ? selectedJob?.title : 'Missing'}
                    </span>
                  </div>
                </div>

                    {project?.embedding_status && project.embedding_status !== 'ready' && project.embedding_status !== 'completed' && (
                  <div className={`p-4 rounded-xl border text-left text-sm ${
                    project.embedding_status === 'failed' 
                      ? 'border-red-500/25 bg-red-950/20 text-red-300'
                      : 'border-amber-500/25 bg-amber-950/20 text-amber-300'
                  }`}>
                    <div className="flex items-start gap-3 w-full">
                      {project.embedding_status === 'failed' ? (
                        <AlertTriangle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
                      ) : (
                        <Loader2 className="w-5 h-5 text-amber-400 animate-spin shrink-0 mt-0.5" />
                      )}
                      <div className="w-full">
                        <p className="font-semibold text-xs uppercase tracking-wider">
                          {project.embedding_status === 'failed'
                            ? 'Indexing Failed'
                            : `Generating Candidate Embeddings (${workerStatus?.current_stage || project.embedding_status})`}
                        </p>
                        <p className="text-xs opacity-80 mt-1">
                          {project.embedding_status === 'failed'
                            ? 'Indexing failed. Your candidate file is still stored — click Retry Indexing to restart without re-uploading.'
                            : 'Embeddings are generating in the background. Analysis will be enabled automatically once indexing completes.'}
                        </p>

                        {/* Retry button for failed state */}
                        {project.embedding_status === 'failed' && (
                          <Button
                            onClick={handleRetryIndexing}
                            disabled={retrying}
                            size="sm"
                            className="mt-3 bg-red-700 hover:bg-red-600 text-white border-0"
                          >
                            {retrying
                              ? <><Loader2 className="w-3.5 h-3.5 mr-2 animate-spin" />Retrying...</>
                              : '↺ Retry Indexing — No Re-upload Needed'}
                          </Button>
                        )}
                        
                        {project.embedding_status !== 'failed' && workerStatus && (
                          <div className="mt-3 space-y-2 w-full">
                            <div className="w-full bg-zinc-800 rounded-full h-1.5 overflow-hidden">
                              <div 
                                className="bg-indigo-500 h-1.5 rounded-full transition-all duration-500" 
                                style={{ width: `${workerStatus.progress_percentage || 0}%` }}
                              />
                            </div>
                            <div className="flex flex-wrap justify-between text-[10px] text-muted-foreground gap-2">
                              <span>Progress: {workerStatus.progress_percentage || 0}% ({workerStatus.processed_candidates || 0}/{workerStatus.total_candidates || 0})</span>
                              {workerStatus.eta && workerStatus.eta !== '00:00:00' && <span>ETA: {workerStatus.eta}</span>}
                              {workerStatus.ram_usage > 0 && <span>RAM: {workerStatus.ram_usage.toFixed(1)}MB / Peak: {workerStatus.peak_ram?.toFixed(1)}MB</span>}
                            </div>
                          </div>
                        )}

                        {project.embedding_status !== 'failed' && (
                          <button
                            onClick={async () => {
                              if (confirm('Are you sure you want to cancel indexing?')) {
                                setCancelling(true);
                                try {
                                  await platformApi.cancelIndexing(projectId);
                                  toast.success('Indexing cancellation requested');
                                  await load();
                                } catch (err: any) {
                                  toast.error(err.message || 'Failed to cancel indexing');
                                } finally {
                                  setCancelling(false);
                                }
                              }
                            }}
                            disabled={cancelling}
                            className="mt-3 text-xs bg-red-950/40 hover:bg-red-900/40 text-red-300 px-3 py-1.5 rounded-lg border border-red-500/20 transition-all focus:outline-none focus:ring-1 focus:ring-red-500/50"
                          >
                            {cancelling ? 'Cancelling...' : 'Cancel Indexing'}
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
                  <select
                    value={performanceMode}
                    onChange={(e) => setPerformanceMode(e.target.value)}
                    className="bg-zinc-900 border border-zinc-700/50 rounded-lg px-4 py-2.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-indigo-500/50 cursor-pointer"
                    disabled={analyzing}
                  >
                    <option value="fast">⚡ Fast (2-5s)</option>
                    <option value="balanced">⚖️ Balanced (5-10s)</option>
                    <option value="deep">🔍 Deep (10-30s)</option>
                  </select>
                  <Button
                    onClick={() => handleAnalyze()}
                    disabled={analyzing || !canRunAnalysis}
                    size="lg"
                    className={`w-full sm:w-auto text-white border-0 shadow-lg transition-all ${
                      canRunAnalysis
                        ? 'bg-gradient-to-r from-indigo-500 to-purple-600 shadow-indigo-500/20 hover:opacity-90'
                        : 'bg-zinc-800 text-zinc-500 cursor-not-allowed shadow-none hover:bg-zinc-800'
                    }`}
                  >
                    {analyzing
                      ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Analysis Running...</>
                      : <><Sparkles className="w-4 h-4 mr-2" />Run AI Analysis</>}
                  </Button>
                </div>
                {!canRunAnalysis && (
                  <p className="text-xs text-amber-400">
                    {!project.candidate_count && !selectedJobId
                      ? 'Please upload candidates and select a job description to begin.'
                      : !project.candidate_count
                      ? 'Please upload candidates first.'
                      : !selectedJobId
                      ? 'Please select or upload a job description first.'
                      : 'Candidate indexing is still in progress. Please wait until indexing completes.'}
                  </p>
                )}
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-6">
              {/* Analysis Status Banner (Requirement 7) */}
              {ranking && (
                <Card className={`border p-4 flex items-start gap-3 rounded-xl transition-all ${
                  ranking.metadata_only_fallback
                    ? 'border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-950/20 text-zinc-800 dark:text-zinc-300'
                    : ranking.ai_enhancement_unavailable
                    ? 'border-blue-200 dark:border-blue-500/20 bg-blue-50 dark:bg-blue-950/10 text-blue-800 dark:text-blue-300'
                    : 'border-indigo-200 dark:border-indigo-500/30 bg-gradient-to-r from-indigo-50/50 to-purple-50/50 dark:from-indigo-950/20 dark:to-purple-950/20 text-indigo-900 dark:text-indigo-300'
                }`}>
                  <CheckCircle2 className={`w-5 h-5 shrink-0 mt-0.5 ${
                    ranking.metadata_only_fallback
                      ? 'text-zinc-600 dark:text-zinc-400'
                      : ranking.ai_enhancement_unavailable
                      ? 'text-blue-600 dark:text-blue-400'
                      : 'text-indigo-600 dark:text-indigo-400'
                  }`} />
                  <div>
                    <h4 className="font-semibold text-sm">
                      Analysis Status: Complete
                    </h4>
                    <p className="text-xs mt-1 opacity-90">
                      {ranking.metadata_only_fallback
                        ? 'Deterministic Ranking (Fast metadata filtering & keyword matching active)'
                        : ranking.ai_enhancement_unavailable
                        ? 'Semantic Ranking Enabled (Embedding-based semantic similarity search active)'
                        : 'AI Assisted Ranking (Full multi-dimensional semantic match & LLM evaluations active)'}
                    </p>
                  </div>
                </Card>
              )}

              {/* Prefilter Statistics Banner */}
              <Card className="border border-indigo-200 dark:border-indigo-500/20 bg-indigo-50/30 dark:bg-indigo-950/15 overflow-hidden">
                <CardHeader className="pb-3 border-b border-indigo-100 dark:border-indigo-500/10">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div>
                      <CardTitle className="text-lg text-indigo-900 dark:text-indigo-300 flex items-center gap-2">
                        <Cpu className="w-4 h-4 text-indigo-500 dark:text-indigo-400" />
                        JD-First Prefilter Statistics
                      </CardTitle>
                      <CardDescription className="text-indigo-700/80 dark:text-indigo-400/70">
                        Category & Experience compatibility analysis on the CPU before embeddings retrieval
                      </CardDescription>
                    </div>
                    {ranking.prefilter_statistics?.top_categories && (
                      <div className="flex flex-wrap gap-1">
                        {ranking.prefilter_statistics.top_categories.map(cat => (
                          <Badge key={cat} variant="outline" className="bg-indigo-100/50 dark:bg-indigo-950/40 text-indigo-700 dark:text-indigo-300 border-indigo-200 dark:border-indigo-500/20">
                            {cat}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>
                </CardHeader>
                <CardContent className="pt-6 space-y-6">
                  {/* Stats Grid (PART 9) */}
                  {(() => {
                    const selectedJob = jobs.find(j => j.id === selectedJobId);
                    const jobOpenings = selectedJob?.openings ?? 5;
                    const recommendedCount = ranking.results?.filter((r: any) => r.recommendation_status === 'recommended').length ?? 0;
                    const backupCount = ranking.results?.filter((r: any) => r.recommendation_status === 'backup').length ?? 0;
                    const recommendedScores = ranking.results?.filter((r: any) => r.recommendation_status === 'recommended').map((r: any) => r.match_percent) ?? [];
                    const avgMatchPct = recommendedScores.length ? (recommendedScores.reduce((a: number, b: number) => a + b, 0) / recommendedScores.length).toFixed(1) : '0.0';
                    return (
                      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                        <div className="bg-zinc-50 dark:bg-zinc-950/45 p-4 rounded-xl border border-indigo-100 dark:border-indigo-500/10 flex flex-col justify-center">
                          <span className="text-xs text-muted-foreground">Open Positions</span>
                          <span className="text-3xl font-extrabold text-amber-600 dark:text-amber-400 mt-1">
                            {jobOpenings}
                          </span>
                          <span className="text-xs text-muted-foreground/60 mt-0.5">Recruiter Target</span>
                        </div>
                        <div className="bg-zinc-50 dark:bg-zinc-950/45 p-4 rounded-xl border border-indigo-100 dark:border-indigo-500/10 flex flex-col justify-center">
                          <span className="text-xs text-muted-foreground">Candidates Uploaded</span>
                          <span className="text-3xl font-extrabold text-indigo-700 dark:text-indigo-300 mt-1">
                            {formatCount(ranking.prefilter_statistics?.total_uploaded ?? ranking.total_candidates)}
                          </span>
                          <span className="text-xs text-muted-foreground/60 mt-0.5">Dataset size</span>
                        </div>
                        <div className="bg-zinc-50 dark:bg-zinc-950/45 p-4 rounded-xl border border-indigo-100 dark:border-indigo-500/10 flex flex-col justify-center">
                          <span className="text-xs text-muted-foreground">Recommended Hires</span>
                          <span className="text-3xl font-extrabold text-green-600 dark:text-green-400 mt-1">
                            {recommendedCount}
                          </span>
                          <span className="text-xs text-green-600 dark:text-green-400/60 mt-0.5">Top Matches</span>
                        </div>
                        <div className="bg-zinc-50 dark:bg-zinc-950/45 p-4 rounded-xl border border-indigo-100 dark:border-indigo-500/10 flex flex-col justify-center">
                          <span className="text-xs text-muted-foreground">Backup Options</span>
                          <span className="text-3xl font-extrabold text-blue-600 dark:text-blue-400 mt-1">
                            {backupCount}
                          </span>
                          <span className="text-xs text-blue-600 dark:text-blue-400/60 mt-0.5">Next best candidates</span>
                        </div>
                        <div className="bg-zinc-50 dark:bg-zinc-950/45 p-4 rounded-xl border border-indigo-100 dark:border-indigo-500/10 flex flex-col justify-center col-span-2 md:col-span-1">
                          <span className="text-xs text-muted-foreground">Average Match %</span>
                          <span className="text-3xl font-extrabold text-emerald-600 dark:text-emerald-400 mt-1">
                            {avgMatchPct}%
                          </span>
                          <span className="text-xs text-muted-foreground/60 mt-0.5">Top Recommended</span>
                        </div>
                      </div>
                    );
                  })()}

                  {/* Funnel Flow Diagram */}
                  <div className="space-y-2">
                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Retrieval Pipeline Funnel</p>
                    <div className="flex flex-col md:flex-row items-center justify-between gap-4 p-5 bg-zinc-50 dark:bg-zinc-950/30 rounded-xl border border-zinc-200 dark:border-zinc-800/80">
                      {/* Step 1: Uploaded */}
                      <div className="flex flex-col items-center text-center">
                        <span className="text-2xl font-extrabold text-indigo-600 dark:text-indigo-400">
                          {formatCount(ranking.prefilter_statistics?.total_uploaded ?? ranking.total_candidates)}
                        </span>
                        <span className="text-[10px] text-muted-foreground uppercase mt-0.5">1. Uploaded</span>
                        <span className="text-[9px] text-muted-foreground/60">Raw Ingestion</span>
                      </div>
                      
                      <div className="text-muted-foreground hidden md:block">→</div>

                      {/* Step 2: Eligible */}
                      <div className="flex flex-col items-center text-center">
                        <span className="text-2xl font-extrabold text-blue-600 dark:text-blue-400">
                          {formatCount(ranking.prefilter_statistics?.eligible ?? ranking.ranked_count)}
                        </span>
                        <span className="text-[10px] text-muted-foreground uppercase mt-0.5">2. Eligible</span>
                        <span className="text-[9px] text-muted-foreground/60">JD-Based Prefilter</span>
                      </div>

                      <div className="text-muted-foreground hidden md:block">→</div>

                      {/* Step 3: Retrieved */}
                      <div className="flex flex-col items-center text-center">
                        <span className="text-2xl font-extrabold text-purple-600 dark:text-purple-400">
                          {formatCount(ranking.metrics?.candidates_retrieved ?? (ranking.prefilter_statistics?.eligible ? Math.min(500, ranking.prefilter_statistics.eligible) : 500))}
                        </span>
                        <span className="text-[10px] text-muted-foreground uppercase mt-0.5">3. Retrieved</span>
                        <span className="text-[9px] text-muted-foreground/60">FAISS Semantic</span>
                      </div>

                      <div className="text-muted-foreground hidden md:block">→</div>

                      {/* Step 4: Scored */}
                      <div className="flex flex-col items-center text-center">
                        <span className="text-2xl font-extrabold text-pink-600 dark:text-pink-400">
                          {formatCount(ranking.metrics?.candidates_scored ?? (ranking.prefilter_statistics?.eligible ? Math.min(100, ranking.prefilter_statistics.eligible) : 100))}
                        </span>
                        <span className="text-[10px] text-muted-foreground uppercase mt-0.5">4. Scored</span>
                        <span className="text-[9px] text-muted-foreground/60">Dimension Scoring</span>
                      </div>

                      <div className="text-muted-foreground hidden md:block">→</div>

                      {/* Step 5: Evaluated */}
                      <div className="flex flex-col items-center text-center">
                        <span className="text-2xl font-extrabold text-emerald-600 dark:text-emerald-400">
                          {formatCount(ranking.metrics?.llm_candidates_evaluated ?? (ranking.prefilter_statistics?.eligible ? Math.min(30, ranking.prefilter_statistics.eligible) : 30))}
                        </span>
                        <span className="text-[10px] text-muted-foreground uppercase mt-0.5">5. Ranked</span>
                        <span className="text-[9px] text-muted-foreground/60">Gemini LLM Review</span>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Sleek horizontal telemetry metrics banner */}
              {ranking.metrics && (
                <div className="bg-indigo-50/50 dark:bg-indigo-950/40 border border-indigo-100 dark:border-indigo-500/25 rounded-xl p-4 text-sm">
                  <div className="flex items-center gap-2 text-indigo-800 dark:text-indigo-300 font-semibold mb-3">
                    <Cpu className="w-4 h-4 text-indigo-500 dark:text-indigo-400" />
                    Pipeline Telemetry & Execution Metrics
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                    <div className="bg-indigo-50/80 dark:bg-indigo-950/30 p-2.5 rounded-lg border border-indigo-100 dark:border-indigo-500/10">
                      <p className="text-xs text-muted-foreground">Filtered / Total</p>
                      <p className="text-lg font-bold text-indigo-900 dark:text-indigo-200">
                        {ranking.metrics.candidates_filtered} / {ranking.metrics.total_candidates}
                      </p>
                    </div>
                    <div className="bg-indigo-50/80 dark:bg-indigo-950/30 p-2.5 rounded-lg border border-indigo-100 dark:border-indigo-500/10">
                      <p className="text-xs text-muted-foreground">Vector Retrieved</p>
                      <p className="text-lg font-bold text-indigo-900 dark:text-indigo-200">
                        {ranking.metrics.candidates_retrieved}
                      </p>
                    </div>
                    <div className="bg-indigo-50/80 dark:bg-indigo-950/30 p-2.5 rounded-lg border border-indigo-100 dark:border-indigo-500/10">
                      <p className="text-xs text-muted-foreground">Deep Scored</p>
                      <p className="text-lg font-bold text-indigo-900 dark:text-indigo-200">
                        {ranking.metrics.candidates_scored}
                      </p>
                    </div>
                    <div className="bg-indigo-50/80 dark:bg-indigo-950/30 p-2.5 rounded-lg border border-indigo-100 dark:border-indigo-500/10">
                      <p className="text-xs text-muted-foreground">LLM Evaluated</p>
                      <p className="text-lg font-bold text-indigo-900 dark:text-indigo-200">
                        {ranking.metrics.llm_candidates_evaluated}
                      </p>
                    </div>
                    <div className="bg-indigo-50/80 dark:bg-indigo-950/30 p-2.5 rounded-lg border border-indigo-100 dark:border-indigo-500/10 col-span-2 md:col-span-1">
                      <p className="text-xs text-muted-foreground">Total Analysis Time</p>
                      <p className="text-lg font-bold text-green-600 dark:text-green-400">
                        {ranking.metrics.total_analysis_time.toFixed(2)}s
                      </p>
                    </div>
                  </div>
                  <div className="mt-3 text-xs text-muted-foreground/80 flex flex-wrap gap-x-4 gap-y-1">
                    <span>Retrieval: {ranking.metrics.retrieval_time.toFixed(2)}s</span>
                    <span>Scoring: {ranking.metrics.ranking_time.toFixed(2)}s</span>
                    <span>LLM Review: {ranking.metrics.llm_time.toFixed(2)}s</span>
                  </div>
                  {ranking.metrics && (ranking.metrics as any).filter_time !== undefined && (
                    <div className="mt-2.5 pt-2.5 border-t border-indigo-100 dark:border-indigo-500/10 text-xs text-indigo-700 dark:text-indigo-400/80 flex flex-wrap gap-x-4 gap-y-1">
                      <span>Role & Exp Filter: {(ranking.metrics as any).filter_time?.toFixed(2)}s</span>
                      <span>Index Lookup: {(ranking.metrics as any).index_lookup_time?.toFixed(2)}s</span>
                      {!(ranking.metrics as any).embedding_time ? null : <span>JD Embedding: {(ranking.metrics as any).embedding_time?.toFixed(2)}s</span>}
                      {!(ranking.metrics as any).faiss_time ? null : <span>FAISS: {(ranking.metrics as any).faiss_time?.toFixed(2)}s</span>}
                      <span>Scoring: {(ranking.metrics as any).scoring_time?.toFixed(2)}s</span>
                      <span>LLM Eval: {(ranking.metrics as any).llm_time?.toFixed(2)}s</span>
                    </div>
                  )}
                </div>
              )}

              {ranking.status === 'no_qualified_candidates' ? (
                <div className="space-y-4">
                  <Card className="border-amber-500/30 bg-amber-500/5">
                    <CardContent className="py-10 text-center">
                      <p className="font-semibold text-lg text-amber-400">No qualified candidates found for this role.</p>
                      <p className="text-sm text-muted-foreground mt-2">
                        All uploaded candidates failed key qualification thresholds or matching critical skills.
                      </p>
                    </CardContent>
                  </Card>

                  {ranking.alternative_candidates && ranking.alternative_candidates.length > 0 && (
                    <Card>
                      <CardHeader>
                        <CardTitle className="text-lg text-indigo-300">Top Alternative Candidates</CardTitle>
                        <CardDescription>
                          These candidates did not meet the eligibility bar but are the closest matches:
                        </CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-2">
                         {ranking.alternative_candidates.slice(0, 5).map(r => (
                          <div
                            key={r.candidate_id}
                            className="flex items-start gap-4 p-3 rounded-lg bg-muted/20 hover:bg-muted/40 transition-colors cursor-pointer group/cand"
                            onClick={() => setSelectedCandidate({
                              id: r.candidate_id,
                              name: (r as any).candidate_name,
                              rankInfo: {
                                rank: r.rank,
                                aiScore: Math.round(r.ai_score * 100),
                                matchPercent: Math.round(r.match_percent),
                                reasoning: r.reasoning || '',
                                hiringReadiness: r.hiring_readiness,
                                strengths: r.strengths || [],
                                weaknesses: r.weaknesses || [],
                                roleMatchPercent: Math.round((r as any).role_match_percent ?? 50),
                                criticalSkillMatchPercent: Math.round((r as any).critical_skill_match_percent ?? 50),
                                experienceMatchPercent: Math.round((r as any).experience_match_percent ?? 50),
                                semanticSimilarityPercent: Math.round((r as any).semantic_similarity_percent ?? 50),
                                criticalSkillCoverage: (r as any).critical_skill_coverage,
                                criticalSkillCoveragePercent: (r as any).critical_skill_coverage_percent,
                              },
                            })}
                          >
                            <div className="w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold shrink-0 bg-muted text-muted-foreground">alt</div>
                            <div className="flex-1 min-w-0">
                              <p className="text-sm font-medium truncate group-hover/cand:text-indigo-400 transition-colors">{r.candidate_name || r.candidate_id}</p>
                              <p className="text-xs text-muted-foreground line-clamp-1">{r.reasoning}</p>
                            </div>
                            <div className="text-right shrink-0">
                              <p className="font-bold text-amber-400">{r.match_percent}%</p>
                              <Badge variant={r.hiring_readiness === 'high' ? 'default' : 'secondary'} className="text-xs capitalize">
                                {r.hiring_readiness}
                              </Badge>
                            </div>
                          </div>
                        ))}
                      </CardContent>
                    </Card>
                  )}
                </div>
              ) : ranking.results.length > 0 ? (
                <Card>
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <div>
                        <CardTitle>Top {ranking.ranked_count} Ranked Candidates</CardTitle>
                        <CardDescription>
                          JD: "{selectedJob?.title}" · Semantic fit + experience + behavioral
                        </CardDescription>
                      </div>
                      <div className="flex gap-2">
                        <Link href={`/ranking?project=${projectId}&ranking=${ranking.id}`}>
                          <Button variant="outline" size="sm">View Full Table</Button>
                        </Link>
                        <Button variant="outline" size="sm" onClick={() => platformApi.export(projectId, ranking.id, 'csv')}>
                          Export CSV
                        </Button>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2">
                      {(() => {
                        const startIdx = (currentPage - 1) * pageSize;
                        const paginated = pageSize === -1 ? ranking.results : ranking.results.slice(startIdx, startIdx + pageSize);
                        return paginated.map(r => (
                          <div
                            key={r.candidate_id}
                            className="flex items-start gap-4 p-3 rounded-lg bg-muted/20 hover:bg-muted/40 transition-colors cursor-pointer group/cand"
                            onClick={() => setSelectedCandidate({
                              id: r.candidate_id,
                              name: (r as any).candidate_name,
                              rankInfo: {
                                rank: r.rank,
                                aiScore: Math.round(r.ai_score * 100),
                                matchPercent: Math.round(r.match_percent),
                                reasoning: r.reasoning || '',
                                hiringReadiness: r.hiring_readiness,
                                strengths: r.strengths || [],
                                weaknesses: r.weaknesses || [],
                                roleMatchPercent: Math.round((r as any).role_match_percent ?? 50),
                                criticalSkillMatchPercent: Math.round((r as any).critical_skill_match_percent ?? 50),
                                experienceMatchPercent: Math.round((r as any).experience_match_percent ?? 50),
                                semanticSimilarityPercent: Math.round((r as any).semantic_similarity_percent ?? 50),
                                criticalSkillCoverage: (r as any).critical_skill_coverage,
                                criticalSkillCoveragePercent: (r as any).critical_skill_coverage_percent,
                              },
                            })}
                          >
                            <div className={`w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${
                              r.rank <= 3 ? 'bg-gradient-to-br from-amber-400 to-orange-500 text-white' : 'bg-muted text-muted-foreground'
                            }`}>#{r.rank}</div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 flex-wrap">
                                <p className="text-sm font-medium truncate group-hover/cand:text-indigo-400 transition-colors">{(r as any).candidate_name || r.candidate_id}</p>
                                {r.eligibility !== undefined && (
                                  <Badge 
                                    variant={r.eligibility ? 'default' : 'outline'} 
                                    className={`text-[10px] px-1.5 py-0 capitalize font-semibold ${
                                      r.eligibility 
                                        ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30 hover:bg-emerald-500/30' 
                                        : 'bg-rose-500/20 text-rose-300 border-rose-500/30 hover:bg-rose-500/30'
                                    }`}
                                    title={r.eligibility_reason}
                                  >
                                    {r.eligibility ? 'Eligible' : 'Ineligible'}
                                  </Badge>
                                )}
                              </div>
                              <p className="text-xs text-muted-foreground line-clamp-1 mt-0.5">{r.reasoning}</p>
                              {r.eligibility_reason && !r.eligibility && (
                                <p className="text-[10px] text-rose-400/80 mt-0.5 line-clamp-1">Reason: {r.eligibility_reason}</p>
                              )}
                            </div>
                            <div className="text-right shrink-0">
                              <p className="font-bold text-green-400">{r.match_percent}%</p>
                              <Badge variant={r.hiring_readiness === 'high' ? 'default' : 'secondary'} className="text-xs capitalize">
                                {r.hiring_readiness}
                              </Badge>
                            </div>
                          </div>
                        ));
                      })()}
                    </div>

                    {/* Pagination Controls */}
                    {ranking.results.length > 0 && (
                      <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-4 border-t border-border mt-4 text-sm">
                        <div className="flex items-center gap-2">
                          <span className="text-muted-foreground text-xs">Show:</span>
                          <select
                            value={pageSize}
                            onChange={(e) => {
                              setPageSize(Number(e.target.value));
                              setCurrentPage(1);
                            }}
                            className="bg-muted border border-border rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500"
                          >
                            <option value={5}>5</option>
                            <option value={10}>10</option>
                            <option value={20}>20</option>
                            <option value={50}>50</option>
                            <option value={-1}>All</option>
                          </select>
                          <span className="text-muted-foreground text-xs ml-2">
                            Showing {pageSize === -1 ? 1 : (currentPage - 1) * pageSize + 1} - {
                              pageSize === -1 
                                ? ranking.results.length 
                                : Math.min(currentPage * pageSize, ranking.results.length)
                            } of {ranking.results.length} candidates
                          </span>
                        </div>
                        {pageSize !== -1 && Math.ceil(ranking.results.length / pageSize) > 1 && (
                          <div className="flex items-center gap-2">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => setCurrentPage(prev => Math.max(prev - 1, 1))}
                              disabled={currentPage === 1}
                              className="h-8 px-2"
                            >
                              Previous
                            </Button>
                            <span className="text-xs text-muted-foreground">
                              Page {currentPage} of {Math.ceil(ranking.results.length / pageSize)}
                            </span>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => setCurrentPage(prev => Math.min(prev + 1, Math.ceil(ranking.results.length / pageSize)))}
                              disabled={currentPage >= Math.ceil(ranking.results.length / pageSize)}
                              className="h-8 px-2"
                            >
                              Next
                            </Button>
                          </div>
                        )}
                      </div>
                    )}
                  </CardContent>
                </Card>
              ) : null}
            </div>
          )}
        </TabsContent>
      </Tabs>
      {projectId && (
        <CandidateDetailSheet
          projectId={projectId}
          candidateId={selectedCandidate?.id ?? null}
          candidateName={selectedCandidate?.name}
          rankInfo={selectedCandidate?.rankInfo}
          onClose={() => setSelectedCandidate(null)}
        />
      )}
    </div>
  );
}
