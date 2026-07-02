'use client';

import { useEffect, useState, useMemo, Suspense, useRef } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { Search, Download, FolderKanban, Trophy, Loader2, Cpu, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { platformApi, type Project, type Ranking, type RankingResult } from '@/lib/platform-api';
import { CandidateDetailSheet } from '@/components/candidates/CandidateDetailSheet';
import { toast } from 'sonner';

function getMatchColor(score: number) {
  if (score >= 75) return 'text-green-400';
  if (score >= 50) return 'text-amber-400';
  return 'text-red-400';
}

function getMatchBg(score: number) {
  if (score >= 75) return 'bg-green-500/15 border-green-500/30';
  if (score >= 50) return 'bg-amber-500/15 border-amber-500/30';
  return 'bg-red-500/15 border-red-500/30';
}

function ScorePill({ value }: { value: number }) {
  return (
    <span className={`inline-flex items-center justify-center w-11 h-8 rounded-full text-sm font-bold border ${getMatchBg(value)} ${getMatchColor(value)}`}>
      {value}
    </span>
  );
}

function getReadinessBadge(r: string) {
  if (r === 'high') return <Badge className="bg-green-500/20 text-green-400 border-green-500/30 text-xs capitalize">{r}</Badge>;
  if (r === 'medium') return <Badge className="bg-amber-500/20 text-amber-400 border-amber-500/30 text-xs capitalize">{r}</Badge>;
  return <Badge className="bg-red-500/20 text-red-400 border-red-500/30 text-xs capitalize">{r}</Badge>;
}

function RankingContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const projectParam = searchParams.get('project');
  const rankingParam = searchParams.get('ranking');

  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>(projectParam || '');
  const [rankingData, setRankingData] = useState<Ranking | null>(null);
  const [loading, setLoading] = useState(true);
  const [rankingLoading, setRankingLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState('rank');

  // Pagination states
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  // Candidate detail sheet
  const [selectedCandidate, setSelectedCandidate] = useState<{ id: string; name?: string; rankInfo?: any } | null>(null);

  // Load all projects
  useEffect(() => {
    platformApi.projects.list()
      .then(ps => {
        setProjects(ps);
        if (!projectParam && ps.length > 0) setSelectedProjectId(ps[0].id);
      })
      .catch(() => toast.error('Failed to load projects'))
      .finally(() => setLoading(false));
  }, [projectParam]);

  const has404Ref = useRef(false);

  useEffect(() => {
    has404Ref.current = false;
  }, [selectedProjectId, rankingParam]);

  // Load ranking for selected project
  useEffect(() => {
    if (!selectedProjectId || has404Ref.current) return;

    // Check if selectedProjectId is in the projects list (once loaded)
    if (projects.length > 0 && !projects.some(p => p.id === selectedProjectId)) {
      toast.error('Project no longer exists.');
      // Auto-select first project if available
      const fallbackId = projects[0]?.id || '';
      setSelectedProjectId(fallbackId);
      if (fallbackId) {
        router.push(`/ranking?project=${fallbackId}`);
      } else {
        router.push('/ranking');
      }
      return;
    }

    setRankingLoading(true);

    // If we have a specific ranking ID from query params, load that
    if (rankingParam && projectParam === selectedProjectId) {
      platformApi.ranking(selectedProjectId, rankingParam)
        .then(setRankingData)
        .catch((err: any) => {
          setRankingData(null);
          if (err.status === 404) {
            has404Ref.current = true;
            toast.error('Ranking or project no longer exists.');
            // Clear reference
            router.push('/ranking');
          }
        })
        .finally(() => {
          if (!has404Ref.current) {
            setRankingLoading(false);
          }
        });
    } else {
      // Otherwise there's no ranking yet for this project
      setRankingData(null);
      setRankingLoading(false);
    }
  }, [selectedProjectId, rankingParam, projectParam, projects, router]);

  const filtered = useMemo(() => {
    if (!rankingData?.results) return [];
    const q = search.toLowerCase();
    let rows = rankingData.results.filter(r => {
      const name = (r as any).candidate_name || '';
      const title = (r as any).current_title || '';
      const location = (r as any).location || '';
      return (
        r.candidate_id.toLowerCase().includes(q) ||
        name.toLowerCase().includes(q) ||
        title.toLowerCase().includes(q) ||
        location.toLowerCase().includes(q) ||
        r.reasoning?.toLowerCase().includes(q) ||
        r.hiring_readiness.toLowerCase().includes(q)
      );
    });
    rows = [...rows].sort((a, b) => {
      if (sortBy === 'score') return b.ai_score - a.ai_score;
      if (sortBy === 'match') return b.match_percent - a.match_percent;
      return a.rank - b.rank;
    });
    return rows;
  }, [rankingData, search, sortBy]);

  useEffect(() => {
    setCurrentPage(1);
  }, [search, sortBy, selectedProjectId]);

  const handleExport = () => {
    if (!rankingData || !selectedProjectId) return;
    platformApi.export(selectedProjectId, rankingData.id, 'csv');
    toast.success('Exporting CSV...');
  };

  const selectedProject = projects.find(p => p.id === selectedProjectId);

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-400" />
      </div>
    );
  }

  if (projects.length === 0) {
    return (
      <div className="p-6 lg:p-8 space-y-6">
        <h1 className="text-3xl font-bold">Rankings</h1>
        <Card>
          <CardContent className="py-16 text-center">
            <FolderKanban className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
            <p className="text-muted-foreground mb-4">No projects yet. Create a project to start ranking candidates.</p>
            <Link href="/projects/new"><Button>Create Project</Button></Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6 lg:p-8 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Rankings</h1>
          <p className="text-muted-foreground mt-1">
            Semantic + experience + behavioral scoring — honeypots filtered out
          </p>
        </div>
        {rankingData && (
          <Button onClick={handleExport} variant="outline">
            <Download className="w-4 h-4 mr-2" /> Export CSV
          </Button>
        )}
      </div>

      {/* Project selector */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm text-muted-foreground font-medium">Project:</span>
        <div className="flex flex-wrap gap-2">
          {projects.map(p => (
            <button
              key={p.id}
              onClick={() => setSelectedProjectId(p.id)}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium border transition-all ${
                selectedProjectId === p.id
                  ? 'bg-indigo-500/20 border-indigo-500/50 text-indigo-300'
                  : 'border-border text-muted-foreground hover:border-indigo-500/30 hover:text-foreground'
              }`}
            >
              <FolderKanban className="w-3.5 h-3.5" />
              {p.name}
              {p.status === 'completed' && <span className="w-1.5 h-1.5 rounded-full bg-green-400" />}
            </button>
          ))}
        </div>
      </div>
      
      {/* Analysis Status Banner (Requirement 7) */}
      {!rankingLoading && rankingData && (
        <Card className={`border p-4 flex items-start gap-3 rounded-xl transition-all ${
          rankingData.metadata_only_fallback
            ? 'border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-950/20 text-zinc-800 dark:text-zinc-300'
            : rankingData.ai_enhancement_unavailable
            ? 'border-blue-200 dark:border-blue-500/20 bg-blue-50 dark:bg-blue-950/10 text-blue-800 dark:text-blue-300'
            : 'border-indigo-200 dark:border-indigo-500/30 bg-gradient-to-r from-indigo-50/50 to-purple-50/50 dark:from-indigo-950/20 dark:to-purple-950/20 text-indigo-900 dark:text-indigo-300'
        }`}>
          <CheckCircle2 className={`w-5 h-5 shrink-0 mt-0.5 ${
            rankingData.metadata_only_fallback
              ? 'text-zinc-600 dark:text-zinc-400'
              : rankingData.ai_enhancement_unavailable
              ? 'text-blue-600 dark:text-blue-400'
              : 'text-indigo-600 dark:text-indigo-400'
          }`} />
          <div>
            <h4 className="font-semibold text-sm">
              Analysis Status: Complete
            </h4>
            <p className="text-xs mt-1 opacity-90">
              {rankingData.metadata_only_fallback
                ? 'Deterministic Ranking (Fast metadata filtering & keyword matching active)'
                : rankingData.ai_enhancement_unavailable
                ? 'Semantic Ranking Enabled (Embedding-based semantic similarity search active)'
                : 'AI Assisted Ranking (Full multi-dimensional semantic match & LLM evaluations active)'}
            </p>
          </div>
        </Card>
      )}

      {/* Telemetry Metrics Banner */}
      {!rankingLoading && rankingData && rankingData.metrics && (
        <div className="bg-indigo-50/50 dark:bg-indigo-950/40 border border-indigo-100 dark:border-indigo-500/25 rounded-xl p-4 text-sm">
          <div className="flex items-center gap-2 text-indigo-800 dark:text-indigo-300 font-semibold mb-3">
            <Cpu className="w-4 h-4 text-indigo-500 dark:text-indigo-400" />
            Pipeline Telemetry & Execution Metrics
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <div className="bg-indigo-50/80 dark:bg-indigo-950/30 p-2.5 rounded-lg border border-indigo-100 dark:border-indigo-500/10">
              <p className="text-xs text-muted-foreground">Filtered / Total</p>
              <p className="text-lg font-bold text-indigo-900 dark:text-indigo-200">
                {rankingData.metrics.candidates_filtered} / {rankingData.metrics.total_candidates}
              </p>
            </div>
            <div className="bg-indigo-50/80 dark:bg-indigo-950/30 p-2.5 rounded-lg border border-indigo-100 dark:border-indigo-500/10">
              <p className="text-xs text-muted-foreground">Vector Retrieved</p>
              <p className="text-lg font-bold text-indigo-900 dark:text-indigo-200">
                {rankingData.metrics.candidates_retrieved}
              </p>
            </div>
            <div className="bg-indigo-50/80 dark:bg-indigo-950/30 p-2.5 rounded-lg border border-indigo-100 dark:border-indigo-500/10">
              <p className="text-xs text-muted-foreground">Deep Scored</p>
              <p className="text-lg font-bold text-indigo-900 dark:text-indigo-200">
                {rankingData.metrics.candidates_scored}
              </p>
            </div>
            <div className="bg-indigo-50/80 dark:bg-indigo-950/30 p-2.5 rounded-lg border border-indigo-100 dark:border-indigo-500/10">
              <p className="text-xs text-muted-foreground">LLM Evaluated</p>
              <p className="text-lg font-bold text-indigo-900 dark:text-indigo-200">
                {rankingData.metrics.llm_candidates_evaluated}
              </p>
            </div>
            <div className="bg-indigo-50/80 dark:bg-indigo-950/30 p-2.5 rounded-lg border border-indigo-100 dark:border-indigo-500/10 col-span-2 md:col-span-1">
              <p className="text-xs text-muted-foreground">Total Analysis Time</p>
              <p className="text-lg font-bold text-green-600 dark:text-green-400">
                {rankingData.metrics.total_analysis_time.toFixed(2)}s
              </p>
            </div>
          </div>
          <div className="mt-3 text-xs text-muted-foreground/80 flex flex-wrap gap-x-4 gap-y-1">
            <span>Retrieval: {rankingData.metrics.retrieval_time.toFixed(2)}s</span>
            <span>Scoring: {rankingData.metrics.ranking_time.toFixed(2)}s</span>
            <span>LLM Review: {rankingData.metrics.llm_time.toFixed(2)}s</span>
          </div>
          {rankingData.metrics && (rankingData.metrics as any).filter_time !== undefined && (
            <div className="mt-2.5 pt-2.5 border-t border-indigo-100 dark:border-indigo-500/10 text-xs text-indigo-700 dark:text-indigo-400/80 flex flex-wrap gap-x-4 gap-y-1">
              <span>Role & Exp Filter: {(rankingData.metrics as any).filter_time?.toFixed(2)}s</span>
              <span>Index Lookup: {(rankingData.metrics as any).index_lookup_time?.toFixed(2)}s</span>
              {!(rankingData.metrics as any).embedding_time ? null : <span>JD Embedding: {(rankingData.metrics as any).embedding_time?.toFixed(2)}s</span>}
              {!(rankingData.metrics as any).faiss_time ? null : <span>FAISS: {(rankingData.metrics as any).faiss_time?.toFixed(2)}s</span>}
              <span>Scoring: {(rankingData.metrics as any).scoring_time?.toFixed(2)}s</span>
              <span>LLM Eval: {(rankingData.metrics as any).llm_time?.toFixed(2)}s</span>
            </div>
          )}
        </div>
      )}

      {/* No ranking for this project */}
      {!rankingLoading && !rankingData && selectedProject && (
        <Card>
          <CardContent className="py-16 text-center">
            <Trophy className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
            <p className="font-medium mb-2">No ranking yet for "{selectedProject.name}"</p>
            <p className="text-sm text-muted-foreground mb-6">
              {selectedProject.candidate_count === 0
                ? 'Upload candidates and a job description to run analysis.'
                : selectedProject.job_count === 0
                ? 'Add a job description to run analysis.'
                : 'Go to the project and click Run AI Analysis.'}
            </p>
            <Link href={`/projects/${selectedProject.id}`}>
              <Button className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white border-0">
                Open Project
              </Button>
            </Link>
          </CardContent>
        </Card>
      )}

      {rankingLoading && (
        <div className="flex justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
        </div>
      )}

      {/* Ranking table */}
      {!rankingLoading && rankingData && rankingData.status !== 'no_qualified_candidates' && (
        <Card className="overflow-hidden">
          <CardHeader className="border-b border-border/50 pb-4">
            <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center justify-between">
              <div>
                <CardTitle className="text-lg">
                  {rankingData.ranked_count} Candidates Ranked
                </CardTitle>
                <p className="text-sm text-muted-foreground mt-0.5">
                  Project: {selectedProject?.name}
                </p>
              </div>
              <div className="flex gap-3 w-full sm:w-auto">
                <div className="relative flex-1 sm:w-64">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    placeholder="Search candidates..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    className="pl-9"
                  />
                </div>
                <Select value={sortBy} onValueChange={v => v && setSortBy(v)}>
                  <SelectTrigger className="w-32">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="rank">By Rank</SelectItem>
                    <SelectItem value="score">By Score</SelectItem>
                    <SelectItem value="match">By Match %</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {/* Table header */}
            <div className="grid grid-cols-[50px_1fr_120px_90px_90px_100px_110px] gap-4 px-6 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wide border-b border-border/50 bg-muted/20">
              <div>Rank</div>
              <div>Candidate</div>
              <div className="hidden md:block">AI Score</div>
              <div>Match %</div>
              <div className="hidden lg:block">Integrity</div>
              <div className="hidden lg:block">Readiness</div>
              <div>Status</div>
            </div>

            {/* Rows */}
            <div className="divide-y divide-border/30">
              {(() => {
                const startIdx = (currentPage - 1) * pageSize;
                const paginated = pageSize === -1 ? filtered : filtered.slice(startIdx, startIdx + pageSize);
                return paginated.map(r => {
                  const isTop3 = r.rank <= 3;
                  const matchStatus = r.match_percent >= 75 ? 'Excellent Match'
                    : r.match_percent >= 50 ? 'Strong Match'
                    : r.match_percent >= 30 ? 'Partial Match'
                    : 'Weak Match';
                  const matchStatusColor = r.match_percent >= 75 ? 'text-green-400 bg-green-500/10 border-green-500/20'
                    : r.match_percent >= 50 ? 'text-blue-400 bg-blue-500/10 border-blue-500/20'
                    : r.match_percent >= 30 ? 'text-amber-400 bg-amber-500/10 border-amber-500/20'
                    : 'text-red-400 bg-red-500/10 border-red-500/20';

                  // Use real name if available, fall back to candidate_id
                  const displayName = (r as any).candidate_name?.trim()
                    ? (r as any).candidate_name
                    : r.candidate_id;
                  const nameParts = displayName.split(' ');
                  const initials = nameParts.length >= 2
                    ? (nameParts[0][0] + nameParts[nameParts.length - 1][0]).toUpperCase()
                    : displayName.slice(0, 2).toUpperCase();

                  return (
                    <div
                      key={r.candidate_id}
                      className={`grid grid-cols-[50px_1fr_120px_90px_90px_100px_110px] gap-4 px-6 py-4 items-center hover:bg-muted/20 transition-colors ${
                        isTop3 ? 'bg-indigo-500/3' : ''
                      }`}
                    >
                      {/* Rank */}
                      <div>
                        <span className={`inline-flex items-center justify-center w-9 h-9 rounded-full text-sm font-bold ${
                          isTop3
                            ? 'bg-gradient-to-br from-amber-400 to-orange-500 text-white shadow-lg shadow-amber-500/20'
                            : 'bg-muted text-muted-foreground'
                        }`}>
                          #{r.rank}
                        </span>
                      </div>

                      {/* Candidate — clickable */}
                      <div
                        className="flex items-center gap-3 min-w-0 cursor-pointer group/cand"
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
                        <div className="w-10 h-10 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white font-bold text-sm shrink-0 group-hover/cand:shadow-lg group-hover/cand:shadow-indigo-500/20 transition-shadow">
                          {initials}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <p className="font-medium text-sm truncate group-hover/cand:text-indigo-300 transition-colors underline-offset-2 group-hover/cand:underline">
                              {displayName}
                            </p>
                            {displayName === "Candidate" && (
                              <Badge className="bg-red-500/20 text-red-400 border-red-500/30 text-[10px] h-4 px-1.5 shrink-0">
                                Metadata Missing
                              </Badge>
                            )}
                            {r.eligibility !== undefined && (
                              <Badge 
                                variant={r.eligibility ? 'default' : 'outline'} 
                                className={`text-[10px] h-4 px-1.5 shrink-0 capitalize font-semibold ${
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
                          {(r as any).current_title && (
                            <p className="text-xs text-indigo-300/80 truncate">
                              {(r as any).current_title}
                              {(r as any).current_company ? ` · ${(r as any).current_company}` : ''}
                            </p>
                          )}
                          {(r as any).location && (
                            <p className="text-xs text-muted-foreground/60 truncate">{(r as any).location}</p>
                          )}
                          {r.reasoning && (
                            <p className="text-xs text-muted-foreground/60 line-clamp-1 mt-0.5 italic">{r.reasoning}</p>
                          )}
                          {r.eligibility_reason && !r.eligibility && (
                            <p className="text-[10px] text-rose-400/80 truncate mt-0.5">Reason: {r.eligibility_reason}</p>
                          )}
                        </div>
                      </div>

                      {/* AI Score */}
                      <div className="hidden md:flex items-center">
                        <ScorePill value={Math.round(r.ai_score * 100)} />
                      </div>

                      {/* Match % */}
                      <div><ScorePill value={Math.round(r.match_percent)} /></div>

                      {/* Integrity */}
                      <div className="hidden lg:flex items-center">
                        <ScorePill value={Math.round(r.integrity_score * 100)} />
                      </div>

                      {/* Readiness */}
                      <div className="hidden lg:block">{getReadinessBadge(r.hiring_readiness)}</div>

                      {/* Status */}
                      <div>
                        <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium border ${matchStatusColor}`}>
                          {matchStatus}
                        </span>
                      </div>
                    </div>
                  );
                });
              })()}
            </div>

            {filtered.length === 0 && (
              <div className="py-12 text-center text-muted-foreground text-sm">
                No candidates match your search.
              </div>
            )}
          </CardContent>
          {/* Pagination Controls */}
          {filtered.length > 0 && (
            <div className="flex flex-col sm:flex-row items-center justify-between gap-4 px-6 py-4 border-t border-border/50 text-sm bg-muted/5">
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground text-xs">Show:</span>
                <select
                  value={pageSize}
                  onChange={(e) => {
                    setPageSize(Number(e.target.value));
                    setCurrentPage(1);
                  }}
                  className="bg-muted border border-border rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500 text-foreground"
                >
                  <option value={5}>5</option>
                  <option value={10}>10</option>
                  <option value={20}>20</option>
                  <option value={50}>50</option>
                  <option value={-1}>All</option>
                </select>
                <span className="text-muted-foreground text-xs ml-2 text-indigo-200">
                  Showing {pageSize === -1 ? 1 : (currentPage - 1) * pageSize + 1} - {
                    pageSize === -1 
                      ? filtered.length 
                      : Math.min(currentPage * pageSize, filtered.length)
                  } of {filtered.length} matching candidates (Total: {rankingData.ranked_count})
                </span>
              </div>
              {pageSize !== -1 && Math.ceil(filtered.length / pageSize) > 1 && (
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
                    Page {currentPage} of {Math.ceil(filtered.length / pageSize)}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setCurrentPage(prev => Math.min(prev + 1, Math.ceil(filtered.length / pageSize)))}
                    disabled={currentPage >= Math.ceil(filtered.length / pageSize)}
                    className="h-8 px-2"
                  >
                    Next
                  </Button>
                </div>
              )}
            </div>
          )}
        </Card>
      )}

      {/* No qualified candidates found */}
      {!rankingLoading && rankingData && rankingData.status === 'no_qualified_candidates' && (
        <div className="space-y-6">
          <Card className="border-amber-500/30 bg-amber-500/5">
            <CardContent className="py-10 text-center">
              <Trophy className="w-12 h-12 mx-auto text-amber-500 mb-4" />
              <p className="font-semibold text-lg text-amber-400">No qualified candidates found for this role.</p>
              <p className="text-sm text-muted-foreground mt-2">
                All uploaded candidates failed key qualification thresholds or matching critical skills.
              </p>
            </CardContent>
          </Card>

          {/* Alternative Candidates */}
          {rankingData.alternative_candidates && rankingData.alternative_candidates.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg text-indigo-300">Top Alternative Candidates</CardTitle>
                <CardDescription>
                  These candidates did not meet the eligibility bar but are the closest matches:
                </CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                <div className="grid grid-cols-[50px_1fr_120px_90px_90px_100px_110px] gap-4 px-6 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wide border-b border-border/50 bg-muted/20">
                  <div>Ref</div>
                  <div>Candidate</div>
                  <div className="hidden md:block">AI Score</div>
                  <div>Match %</div>
                  <div className="hidden lg:block">Integrity</div>
                  <div className="hidden lg:block">Readiness</div>
                  <div>Status</div>
                </div>
                <div className="divide-y divide-border/30">
                  {rankingData.alternative_candidates.map((r, idx) => {
                    const matchStatus = r.match_percent >= 75 ? 'Excellent Match'
                      : r.match_percent >= 50 ? 'Strong Match'
                      : r.match_percent >= 30 ? 'Partial Match'
                      : 'Weak Match';
                    const matchStatusColor = r.match_percent >= 75 ? 'text-green-400 bg-green-500/10 border-green-500/20'
                      : r.match_percent >= 50 ? 'text-blue-400 bg-blue-500/10 border-blue-500/20'
                      : r.match_percent >= 30 ? 'text-amber-400 bg-amber-500/10 border-amber-500/20'
                      : 'text-red-400 bg-red-500/10 border-red-500/20';

                    const displayName = r.candidate_name?.trim() ? r.candidate_name : r.candidate_id;
                    const nameParts = displayName.split(' ');
                    const initials = nameParts.length >= 2
                      ? (nameParts[0][0] + nameParts[nameParts.length - 1][0]).toUpperCase()
                      : displayName.slice(0, 2).toUpperCase();

                    return (
                      <div
                        key={r.candidate_id}
                        className="grid grid-cols-[50px_1fr_120px_90px_90px_100px_110px] gap-4 px-6 py-4 items-center hover:bg-muted/20 transition-colors"
                      >
                        <div>
                          <span className="inline-flex items-center justify-center w-9 h-9 rounded-full text-sm font-bold bg-muted text-muted-foreground">
                            alt
                          </span>
                        </div>
                        <div
                          className="flex items-center gap-3 min-w-0 cursor-pointer group/cand"
                          onClick={() => setSelectedCandidate({
                            id: r.candidate_id,
                            name: r.candidate_name,
                            rankInfo: {
                              rank: 'alt',
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
                          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-indigo-500/50 to-purple-600/50 flex items-center justify-center text-white font-bold text-sm shrink-0">
                            {initials}
                          </div>
                          <div className="min-w-0">
                            <p className="font-medium text-sm truncate group-hover/cand:text-indigo-300 transition-colors underline-offset-2 group-hover/cand:underline">
                              {displayName}
                            </p>
                            {r.current_title && (
                              <p className="text-xs text-indigo-300/80 truncate">
                                {r.current_title}
                                {r.current_company ? ` · ${r.current_company}` : ''}
                              </p>
                            )}
                            {r.location && (
                              <p className="text-xs text-muted-foreground/60 truncate">{r.location}</p>
                            )}
                          </div>
                        </div>
                        <div className="hidden md:flex items-center">
                          <ScorePill value={Math.round(r.ai_score * 100)} />
                        </div>
                        <div><ScorePill value={Math.round(r.match_percent)} /></div>
                        <div className="hidden lg:flex items-center">
                          <ScorePill value={Math.round(r.integrity_score * 100)} />
                        </div>
                        <div className="hidden lg:block">{getReadinessBadge(r.hiring_readiness)}</div>
                        <div>
                          <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium border ${matchStatusColor}`}>
                            {matchStatus}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Candidate detail sheet */}
      {selectedProjectId && (
        <CandidateDetailSheet
          projectId={selectedProjectId}
          candidateId={selectedCandidate?.id ?? null}
          candidateName={selectedCandidate?.name}
          rankInfo={selectedCandidate?.rankInfo}
          onClose={() => setSelectedCandidate(null)}
        />
      )}
    </div>
  );
}

export default function RankingPage() {
  return (
    <Suspense fallback={<div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-indigo-400" /></div>}>
      <RankingContent />
    </Suspense>
  );
}
