'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, RadarChart, PolarGrid, PolarAngleAxis, Radar } from 'recharts';
import { BarChart3, FolderKanban, Loader2, Info, TrendingUp, Users, Target, Zap } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { platformApi, type Project, type Analytics } from '@/lib/platform-api';
import { toast } from 'sonner';

const CHART_COLORS = ['#818cf8', '#a78bfa', '#34d399', '#fbbf24', '#fb923c', '#60a5fa', '#f472b6'];

const tooltipStyle = {
  contentStyle: {
    backgroundColor: 'hsl(222.2 84% 6%)',
    border: '1px solid hsl(217.2 32.6% 20%)',
    borderRadius: '0.75rem',
    color: '#e2e8f0',
    fontSize: '13px',
  },
  labelStyle: { color: '#94a3b8' },
  itemStyle: { color: '#e2e8f0' },
};

function MetricTile({ label, value, sub, color }: { label: string; value: string | number; sub?: string; color: string }) {
  return (
    <div className={`p-5 rounded-2xl border ${color} flex flex-col gap-1`}>
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</p>
      <p className="text-3xl font-bold">{value}</p>
      {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
    </div>
  );
}

export default function AnalyticsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);

  useEffect(() => {
    platformApi.projects.list()
      .then(ps => {
        setProjects(ps);
        if (ps.length > 0) setSelectedId(ps[0].id);
      })
      .catch(() => toast.error('Failed to load projects'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    setAnalyticsLoading(true);
    setAnalytics(null);
    platformApi.analytics(selectedId)
      .then(setAnalytics)
      .catch(() => setAnalytics(null))
      .finally(() => setAnalyticsLoading(false));
  }, [selectedId]);

  const selectedProject = projects.find(p => p.id === selectedId);

  if (loading) {
    return <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-indigo-400" /></div>;
  }

  const radarData = analytics ? [
    { subject: 'Uploaded', A: analytics.hiring_funnel.uploaded },
    { subject: 'Analyzed', A: analytics.hiring_funnel.analyzed },
    { subject: 'Ranked', A: analytics.hiring_funnel.ranked },
    { subject: 'Shortlisted', A: analytics.hiring_funnel.shortlisted },
  ] : [];

  const qualityData = analytics ? [
    { name: 'High Fit', value: analytics.quality_breakdown.high || 0, color: '#34d399' },
    { name: 'Medium Fit', value: analytics.quality_breakdown.medium || 0, color: '#fbbf24' },
    { name: 'Low Fit', value: analytics.quality_breakdown.low || 0, color: '#fb923c' },
  ] : [];

  return (
    <div className="p-6 lg:p-8 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold">Analytics</h1>
        <p className="text-muted-foreground mt-1">Project-level insights into your candidate pool</p>
      </div>

      {/* Disclaimer */}
      <div className="flex items-start gap-3 p-4 rounded-xl bg-blue-500/8 border border-blue-500/20">
        <Info className="w-5 h-5 text-blue-400 shrink-0 mt-0.5" />
        <div className="text-sm">
          <p className="font-medium text-blue-300">About these analytics</p>
          <p className="text-muted-foreground mt-0.5">
            Charts reflect candidates uploaded to the selected project. Skill distribution counts unique skill names across all candidate profiles.
            Experience distribution is based on self-reported years of experience. Quality breakdown requires a completed AI analysis run.
            All data is scoped to the selected project — switch projects using the tabs below.
          </p>
        </div>
      </div>

      {projects.length === 0 ? (
        <Card>
          <CardContent className="py-16 text-center">
            <BarChart3 className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
            <p className="text-muted-foreground mb-4">Create a project and upload candidates to see analytics.</p>
            <Link href="/projects/new"><Button>Create Project</Button></Link>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Project tabs */}
          <div className="flex flex-wrap gap-2">
            {projects.map(p => (
              <button
                key={p.id}
                onClick={() => setSelectedId(p.id)}
                className={`inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium border transition-all ${
                  selectedId === p.id
                    ? 'bg-indigo-500/20 border-indigo-500/50 text-indigo-300'
                    : 'border-border text-muted-foreground hover:border-indigo-500/30 hover:text-foreground'
                }`}
              >
                <FolderKanban className="w-3.5 h-3.5" />
                {p.name}
                <Badge variant="secondary" className="text-xs">{p.candidate_count}</Badge>
              </button>
            ))}
          </div>

          {analyticsLoading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
            </div>
          ) : !analytics || !selectedProject ? (
            <Card>
              <CardContent className="py-12 text-center">
                <p className="text-muted-foreground">No data available for this project yet.</p>
                <Link href={`/projects/${selectedId}`} className="mt-4 inline-block">
                  <Button variant="outline" size="sm">Open Project</Button>
                </Link>
              </CardContent>
            </Card>
          ) : (
            <>
              {/* Metric tiles */}
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <MetricTile
                  label="Total Candidates"
                  value={analytics.hiring_funnel.uploaded.toLocaleString()}
                  sub="in this project"
                  color="border-indigo-500/20 bg-indigo-500/5"
                />
                <MetricTile
                  label="Analyzed"
                  value={analytics.hiring_funnel.analyzed.toLocaleString()}
                  sub="processed by AI"
                  color="border-purple-500/20 bg-purple-500/5"
                />
                <MetricTile
                  label="Ranked"
                  value={analytics.hiring_funnel.ranked.toLocaleString()}
                  sub="scored across 6 dimensions"
                  color="border-blue-500/20 bg-blue-500/5"
                />
                <MetricTile
                  label="Shortlisted"
                  value={analytics.hiring_funnel.shortlisted.toLocaleString()}
                  sub="high fit candidates"
                  color="border-green-500/20 bg-green-500/5"
                />
              </div>

              {/* Charts row 1 */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Skill Distribution */}
                <Card className="overflow-hidden">
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-lg bg-indigo-500/20 flex items-center justify-center">
                        <Zap className="w-4 h-4 text-indigo-400" />
                      </div>
                      <div>
                        <CardTitle className="text-base">Top Skills in Pool</CardTitle>
                        <CardDescription className="text-xs">Most common skills across all candidates</CardDescription>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    {analytics.skill_distribution.length === 0 ? (
                      <div className="h-48 flex items-center justify-center text-muted-foreground text-sm">
                        No skill data yet — upload candidates with skill information.
                      </div>
                    ) : (
                      <div className="h-56">
                        <ResponsiveContainer width="100%" height="100%">
                          <BarChart data={analytics.skill_distribution.slice(0, 8)} barSize={28}>
                            <CartesianGrid strokeDasharray="3 3" stroke="hsl(217.2 32.6% 17%)" vertical={false} />
                            <XAxis
                              dataKey="skill"
                              tick={{ fill: '#94a3b8', fontSize: 11 }}
                              tickLine={false}
                              axisLine={false}
                              angle={-20}
                              textAnchor="end"
                              height={45}
                            />
                            <YAxis
                              tick={{ fill: '#94a3b8', fontSize: 11 }}
                              tickLine={false}
                              axisLine={false}
                              width={30}
                            />
                            <Tooltip {...tooltipStyle} formatter={(v) => [v, 'Candidates']} />
                            <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                              {analytics.skill_distribution.slice(0, 8).map((_, i) => (
                                <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                              ))}
                            </Bar>
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                    )}
                  </CardContent>
                </Card>

                {/* Experience Distribution */}
                <Card className="overflow-hidden">
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-lg bg-purple-500/20 flex items-center justify-center">
                        <TrendingUp className="w-4 h-4 text-purple-400" />
                      </div>
                      <div>
                        <CardTitle className="text-base">Experience Distribution</CardTitle>
                        <CardDescription className="text-xs">Candidates grouped by years of experience</CardDescription>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    {analytics.experience_distribution.every(d => d.count === 0) ? (
                      <div className="h-48 flex items-center justify-center text-muted-foreground text-sm">
                        No experience data yet.
                      </div>
                    ) : (
                      <div className="h-56">
                        <ResponsiveContainer width="100%" height="100%">
                          <BarChart data={analytics.experience_distribution} layout="vertical" barSize={22}>
                            <CartesianGrid strokeDasharray="3 3" stroke="hsl(217.2 32.6% 17%)" horizontal={false} />
                            <XAxis
                              type="number"
                              tick={{ fill: '#94a3b8', fontSize: 11 }}
                              tickLine={false}
                              axisLine={false}
                            />
                            <YAxis
                              dataKey="range"
                              type="category"
                              tick={{ fill: '#94a3b8', fontSize: 11 }}
                              tickLine={false}
                              axisLine={false}
                              width={80}
                            />
                            <Tooltip {...tooltipStyle} formatter={(v) => [v, 'Candidates']} />
                            <Bar dataKey="count" radius={[0, 6, 6, 0]}>
                              {analytics.experience_distribution.map((_, i) => (
                                <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                              ))}
                            </Bar>
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>

              {/* Charts row 2 */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Quality Breakdown */}
                <Card className="overflow-hidden">
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-lg bg-green-500/20 flex items-center justify-center">
                        <Target className="w-4 h-4 text-green-400" />
                      </div>
                      <div>
                        <CardTitle className="text-base">Candidate Quality Breakdown</CardTitle>
                        <CardDescription className="text-xs">Based on AI analysis results — requires completed ranking</CardDescription>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    {qualityData.every(d => d.value === 0) ? (
                      <div className="py-8 text-center">
                        <p className="text-sm text-muted-foreground mb-3">Run AI analysis to see quality breakdown.</p>
                        <Link href={`/projects/${selectedId}`}>
                          <Button variant="outline" size="sm">Run Analysis</Button>
                        </Link>
                      </div>
                    ) : (
                      <div className="space-y-3 py-2">
                        {qualityData.map(q => {
                          const total = qualityData.reduce((s, d) => s + d.value, 0);
                          const pct = total > 0 ? Math.round((q.value / total) * 100) : 0;
                          return (
                            <div key={q.name} className="space-y-1.5">
                              <div className="flex items-center justify-between text-sm">
                                <span className="font-medium" style={{ color: q.color }}>{q.name}</span>
                                <span className="text-muted-foreground">{q.value} candidates ({pct}%)</span>
                              </div>
                              <div className="h-2.5 rounded-full bg-muted/40 overflow-hidden">
                                <div
                                  className="h-full rounded-full transition-all duration-700"
                                  style={{ width: `${pct}%`, backgroundColor: q.color }}
                                />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </CardContent>
                </Card>

                {/* Hiring Funnel Radar */}
                <Card className="overflow-hidden">
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-lg bg-amber-500/20 flex items-center justify-center">
                        <Users className="w-4 h-4 text-amber-400" />
                      </div>
                      <div>
                        <CardTitle className="text-base">Hiring Funnel</CardTitle>
                        <CardDescription className="text-xs">Candidates at each stage of the pipeline</CardDescription>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="h-48">
                      <ResponsiveContainer width="100%" height="100%">
                        <RadarChart data={radarData}>
                          <PolarGrid stroke="hsl(217.2 32.6% 20%)" />
                          <PolarAngleAxis
                            dataKey="subject"
                            tick={{ fill: '#94a3b8', fontSize: 12 }}
                          />
                          <Radar
                            name="Candidates"
                            dataKey="A"
                            stroke="#818cf8"
                            fill="#818cf8"
                            fillOpacity={0.25}
                            strokeWidth={2}
                          />
                          <Tooltip {...tooltipStyle} />
                        </RadarChart>
                      </ResponsiveContainer>
                    </div>
                    {/* Funnel legend */}
                    <div className="grid grid-cols-2 gap-2 mt-3 pt-3 border-t border-border/40">
                      {[
                        { label: 'Uploaded', value: analytics.hiring_funnel.uploaded, color: '#818cf8' },
                        { label: 'Analyzed', value: analytics.hiring_funnel.analyzed, color: '#a78bfa' },
                        { label: 'Ranked', value: analytics.hiring_funnel.ranked, color: '#60a5fa' },
                        { label: 'Shortlisted', value: analytics.hiring_funnel.shortlisted, color: '#34d399' },
                      ].map(item => (
                        <div key={item.label} className="flex items-center gap-2 text-xs">
                          <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: item.color }} />
                          <span className="text-muted-foreground">{item.label}:</span>
                          <span className="font-semibold" style={{ color: item.color }}>{item.value}</span>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
