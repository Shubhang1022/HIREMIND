'use client';

import { useEffect, useState, useMemo } from 'react';
import Link from 'next/link';
import {
  Search, Users, FolderKanban, Loader2, ChevronDown, ChevronRight,
  ChevronLeft, ChevronRight as ChevronRightIcon, X, Check,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { platformApi, type Project, type CandidateRow } from '@/lib/platform-api';
import { CandidateDetailSheet } from '@/components/candidates/CandidateDetailSheet';
import { toast } from 'sonner';

interface CandidateListState {
  open: boolean;
  loading: boolean;
  data: CandidateRow[];
  total: number;
  page: number;
  pages: number;
  search: string;
}

export default function CandidatesPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [globalSearch, setGlobalSearch] = useState('');
  const [expandedProjects, setExpandedProjects] = useState<Set<string>>(new Set());
  const [candidateState, setCandidateState] = useState<Record<string, CandidateListState>>({});

  // Detail sheet
  const [selectedCandidate, setSelectedCandidate] = useState<{ id: string; projectId: string; name?: string } | null>(null);

  useEffect(() => {
    platformApi.projects.list()
      .then(ps => {
        setProjects(ps);
        setExpandedProjects(new Set(ps.map(p => p.id)));
      })
      .catch(() => toast.error('Failed to load projects'))
      .finally(() => setLoading(false));
  }, []);

  const toggleProject = (id: string) => {
    setExpandedProjects(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const loadCandidates = async (projectId: string, page = 1, search = '') => {
    setCandidateState(prev => ({
      ...prev,
      [projectId]: {
        ...(prev[projectId] || { open: false, data: [], total: 0, page: 1, pages: 1, search: '' }),
        loading: true,
        open: true,
        page,
        search,
      },
    }));
    try {
      const res = await platformApi.candidates.list(projectId, { page, pageSize: 20, search });
      setCandidateState(prev => ({
        ...prev,
        [projectId]: {
          open: true,
          loading: false,
          data: res.candidates,
          total: res.total,
          page: res.page,
          pages: res.pages,
          search,
        },
      }));
    } catch {
      toast.error('Failed to load candidates');
      setCandidateState(prev => ({
        ...prev,
        [projectId]: {
          ...(prev[projectId] || { open: true, data: [], total: 0, page: 1, pages: 1, search: '' }),
          loading: false,
        },
      }));
    }
  };

  const toggleCandidateTable = (projectId: string) => {
    const state = candidateState[projectId];
    if (state?.open) {
      setCandidateState(prev => ({ ...prev, [projectId]: { ...prev[projectId], open: false } }));
    } else {
      loadCandidates(projectId, 1, '');
    }
  };

  const totalCandidates = projects.reduce((sum, p) => sum + p.candidate_count, 0);

  const filteredProjects = useMemo(() => {
    if (!globalSearch.trim()) return projects;
    const q = globalSearch.toLowerCase();
    return projects.filter(p =>
      p.name.toLowerCase().includes(q) ||
      p.description?.toLowerCase().includes(q)
    );
  }, [projects, globalSearch]);

  if (loading) {
    return <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-indigo-400" /></div>;
  }

  return (
    <div className="p-6 lg:p-8 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Candidates</h1>
          <p className="text-muted-foreground mt-1">Browse candidates by project</p>
        </div>
        <div className="flex items-center gap-2 px-4 py-2 rounded-full bg-muted/50 border border-border/50">
          <Users className="w-4 h-4 text-indigo-400" />
          <span className="text-sm font-medium">{totalCandidates.toLocaleString()} total candidates</span>
        </div>
      </div>

      {/* Global search */}
      <div className="relative max-w-xl">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
        <Input
          placeholder="Search by project name or description..."
          value={globalSearch}
          onChange={e => setGlobalSearch(e.target.value)}
          className="pl-10 bg-muted/30"
        />
      </div>

      {projects.length === 0 ? (
        <Card>
          <CardContent className="py-16 text-center">
            <FolderKanban className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
            <p className="text-muted-foreground mb-4">No projects yet.</p>
            <Link href="/projects/new">
              <Button className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white border-0">Create Project</Button>
            </Link>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {filteredProjects.map(project => {
            const cs = candidateState[project.id];
            return (
              <Card key={project.id} className="overflow-hidden">
                {/* Project header */}
                <button
                  onClick={() => toggleProject(project.id)}
                  className="w-full px-6 py-4 flex items-center justify-between hover:bg-muted/20 transition-colors text-left"
                >
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-indigo-500/20 to-purple-500/20 border border-indigo-500/20 flex items-center justify-center">
                      <FolderKanban className="w-5 h-5 text-indigo-400" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="font-semibold">{project.name}</h3>
                        <Badge variant={project.status === 'completed' ? 'default' : 'secondary'} className="text-xs capitalize">
                          {project.status}
                        </Badge>
                      </div>
                      {project.description && (
                        <p className="text-sm text-muted-foreground mt-0.5">{project.description}</p>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-4 shrink-0">
                    <div className="text-right hidden sm:block">
                      <p className="text-sm font-medium">{project.candidate_count.toLocaleString()} candidates</p>
                      <p className="text-xs text-muted-foreground">{project.job_count} job description{project.job_count !== 1 ? 's' : ''}</p>
                    </div>
                    {expandedProjects.has(project.id)
                      ? <ChevronDown className="w-4 h-4 text-muted-foreground" />
                      : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
                  </div>
                </button>

                {/* Expanded section */}
                {expandedProjects.has(project.id) && (
                  <div className="border-t border-border/50">
                    {project.candidate_count === 0 ? (
                      <div className="px-6 py-8 text-center">
                        <p className="text-sm text-muted-foreground mb-3">No candidates uploaded yet.</p>
                        <Link href={`/projects/${project.id}`}>
                          <Button variant="outline" size="sm">Upload Candidates</Button>
                        </Link>
                      </div>
                    ) : (
                      <div className="px-6 py-4 space-y-4">
                        {/* Stats row */}
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                          {[
                            { label: 'Candidates', value: project.candidate_count, color: 'text-indigo-400' },
                            { label: 'Job Descriptions', value: project.job_count, color: 'text-purple-400' },
                            { label: 'Status', value: project.status, color: 'text-green-400' },
                            { label: 'Updated', value: new Date(project.updated_at).toLocaleDateString(), color: 'text-muted-foreground' },
                          ].map(stat => (
                            <div key={stat.label} className="p-3 rounded-lg bg-muted/20 border border-border/40 text-center">
                              <p className={`text-lg font-bold capitalize ${stat.color}`}>{stat.value}</p>
                              <p className="text-xs text-muted-foreground mt-0.5">{stat.label}</p>
                            </div>
                          ))}
                        </div>

                        {/* Action buttons */}
                        <div className="flex flex-wrap gap-2">
                          <Link href={`/projects/${project.id}`}>
                            <Button variant="outline" size="sm">
                              <FolderKanban className="w-3.5 h-3.5 mr-1.5" /> Open Project
                            </Button>
                          </Link>
                          {project.status === 'completed' && (
                            <Link href={`/ranking?project=${project.id}`}>
                              <Button variant="outline" size="sm">View Rankings</Button>
                            </Link>
                          )}
                          <Button
                            size="sm"
                            variant={cs?.open ? 'default' : 'outline'}
                            onClick={() => toggleCandidateTable(project.id)}
                            className={cs?.open ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/30 hover:bg-indigo-500/30' : ''}
                          >
                            {cs?.open ? (
                              <><X className="w-3.5 h-3.5 mr-1.5" /> Hide Candidates</>
                            ) : (
                              <><Users className="w-3.5 h-3.5 mr-1.5" /> View All Candidates</>
                            )}
                          </Button>
                        </div>

                        {/* Candidate table */}
                        {cs?.open && (
                          <div className="space-y-3 mt-2">
                            {/* Table search */}
                            <div className="relative max-w-sm">
                              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
                              <Input
                                placeholder="Search candidates in this project..."
                                value={cs.search}
                                onChange={e => loadCandidates(project.id, 1, e.target.value)}
                                className="pl-9 h-8 text-sm"
                              />
                            </div>

                            {cs.loading ? (
                              <div className="flex justify-center py-8">
                                <Loader2 className="w-5 h-5 animate-spin text-indigo-400" />
                              </div>
                            ) : cs.data.length === 0 ? (
                              <p className="text-sm text-muted-foreground text-center py-6">
                                {cs.search ? 'No candidates match your search.' : 'No candidates found.'}
                              </p>
                            ) : (
                              <>
                                <div className="rounded-xl border border-border/50 overflow-hidden">
                                  {/* Table header */}
                                  <div className="grid grid-cols-[2fr_2fr_1.5fr_1fr_2fr_80px_80px] gap-3 px-4 py-2.5 bg-muted/30 text-xs font-semibold text-muted-foreground uppercase tracking-wide border-b border-border/50">
                                    <div>Name</div>
                                    <div>Role / Company</div>
                                    <div>Location</div>
                                    <div>Exp</div>
                                    <div>Top Skills</div>
                                    <div>Notice</div>
                                    <div>Open</div>
                                  </div>

                                  {/* Rows */}
                                  <div className="divide-y divide-border/30">
                                    {cs.data.map(c => {
                                      const nameParts = (c.name || '').split(' ');
                                      const initials = nameParts.length >= 2
                                        ? (nameParts[0][0] + nameParts[nameParts.length - 1][0]).toUpperCase()
                                        : (c.name || c.candidate_id).slice(0, 2).toUpperCase();
                                      return (
                                        <div
                                          key={c.candidate_id}
                                          className="grid grid-cols-[2fr_2fr_1.5fr_1fr_2fr_80px_80px] gap-3 px-4 py-3 items-center hover:bg-muted/20 transition-colors text-sm cursor-pointer group/row"
                                          onClick={() => setSelectedCandidate({ id: c.candidate_id, projectId: project.id, name: c.name })}
                                        >
                                          {/* Name */}
                                          <div className="flex items-center gap-2 min-w-0">
                                            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500/60 to-purple-600/60 flex items-center justify-center text-white text-xs font-bold shrink-0">
                                              {initials}
                                            </div>
                                            <div className="min-w-0">
                                              <p className="font-medium truncate">{c.name || '—'}</p>
                                              <p className="text-xs text-muted-foreground truncate">{c.candidate_id}</p>
                                            </div>
                                          </div>

                                          {/* Role / Company */}
                                          <div className="min-w-0">
                                            <p className="truncate font-medium">{c.current_title || '—'}</p>
                                            <p className="text-xs text-muted-foreground truncate">{c.current_company || ''}</p>
                                          </div>

                                          {/* Location */}
                                          <p className="text-muted-foreground truncate text-xs">{c.location || '—'}</p>

                                          {/* Experience */}
                                          <p className="font-medium">
                                            {c.years_of_experience ? `${c.years_of_experience}y` : '—'}
                                          </p>

                                          {/* Skills */}
                                          <div className="flex flex-wrap gap-1">
                                            {c.top_skills.slice(0, 3).map(s => (
                                              <span key={s} className="px-1.5 py-0.5 rounded text-xs bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 truncate max-w-[80px]">
                                                {s}
                                              </span>
                                            ))}
                                          </div>

                                          {/* Notice */}
                                          <p className="text-xs text-muted-foreground text-center">
                                            {c.notice_period_days != null ? `${c.notice_period_days}d` : '—'}
                                          </p>

                                          {/* Open to work */}
                                          <div className="flex justify-center">
                                            {c.open_to_work ? (
                                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-500/15 text-green-400 text-xs border border-green-500/20">
                                                <Check className="w-3 h-3" /> Yes
                                              </span>
                                            ) : (
                                              <span className="text-xs text-muted-foreground">No</span>
                                            )}
                                          </div>
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>

                                {/* Pagination */}
                                {cs.pages > 1 && (
                                  <div className="flex items-center justify-between text-sm">
                                    <p className="text-muted-foreground text-xs">
                                      Showing {((cs.page - 1) * 20) + 1}–{Math.min(cs.page * 20, cs.total)} of {cs.total} candidates
                                    </p>
                                    <div className="flex items-center gap-2">
                                      <Button
                                        variant="outline" size="sm"
                                        disabled={cs.page <= 1}
                                        onClick={() => loadCandidates(project.id, cs.page - 1, cs.search)}
                                      >
                                        <ChevronLeft className="w-3.5 h-3.5" />
                                      </Button>
                                      <span className="text-xs text-muted-foreground px-2">
                                        Page {cs.page} of {cs.pages}
                                      </span>
                                      <Button
                                        variant="outline" size="sm"
                                        disabled={cs.page >= cs.pages}
                                        onClick={() => loadCandidates(project.id, cs.page + 1, cs.search)}
                                      >
                                        <ChevronRightIcon className="w-3.5 h-3.5" />
                                      </Button>
                                    </div>
                                  </div>
                                )}
                              </>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </Card>
            );
          })}

          {filteredProjects.length === 0 && (
            <Card>
              <CardContent className="py-12 text-center">
                <p className="text-muted-foreground">No projects match "{globalSearch}"</p>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      <p className="text-xs text-muted-foreground text-center pb-4">
        💡 Candidates are organized by project. Use "View All Candidates" on any project to browse its full candidate list.
      </p>

      {/* Candidate detail sheet */}
      {selectedCandidate && (
        <CandidateDetailSheet
          projectId={selectedCandidate.projectId}
          candidateId={selectedCandidate.id}
          candidateName={selectedCandidate.name}
          onClose={() => setSelectedCandidate(null)}
        />
      )}
    </div>
  );
}
