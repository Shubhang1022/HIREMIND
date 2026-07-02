'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { FolderKanban, Users, Briefcase, Plus, ArrowRight, Loader2 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { MetricCard } from '@/components/ui/MetricCard';
import { platformApi, type Project } from '@/lib/platform-api';
import { toast } from 'sonner';

export default function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    platformApi.projects.list()
      .then(setProjects)
      .catch(() => toast.error('Failed to load projects'))
      .finally(() => setLoading(false));
  }, []);

  const totalCandidates = projects.reduce((s, p) => s + p.candidate_count, 0);
  const totalJobs = projects.reduce((s, p) => s + p.job_count, 0);
  const activeProjects = projects.filter(p => p.status === 'active' || p.status === 'completed').length;

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-400" />
      </div>
    );
  }

  return (
    <div className="p-6 lg:p-8 space-y-8">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Dashboard</h1>
          <p className="text-muted-foreground mt-1">Overview of your hiring projects and AI analysis.</p>
        </div>
        <Link href="/projects/new">
          <Button className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white border-0">
            <Plus className="w-4 h-4 mr-2" /> New Project
          </Button>
        </Link>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard title="Total Projects" value={projects.length} icon={<FolderKanban className="w-5 h-5" />} trend={`${activeProjects} active`} />
        <MetricCard title="Candidates" value={totalCandidates.toLocaleString()} icon={<Users className="w-5 h-5" />} trend="Across all projects" />
        <MetricCard title="Job Descriptions" value={totalJobs} icon={<Briefcase className="w-5 h-5" />} trend="Ready for analysis" />
        <MetricCard title="AI Analyses" value={projects.filter(p => p.status === 'completed').length} icon={<ArrowRight className="w-5 h-5" />} trend="Completed" />
      </div>

      {projects.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-16">
            <FolderKanban className="w-12 h-12 text-muted-foreground mb-4" />
            <h3 className="text-lg font-semibold mb-2">No projects yet</h3>
            <p className="text-muted-foreground text-center max-w-md mb-6">
              Create your first hiring project, upload candidate data and a job description, then let AI rank your shortlist.
            </p>
            <Link href="/projects/new">
              <Button><Plus className="w-4 h-4 mr-2" /> Create Project</Button>
            </Link>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Recent Projects</CardTitle>
            <Link href="/projects"><Button variant="ghost" size="sm">View All</Button></Link>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {projects.slice(0, 5).map(p => (
                <Link key={p.id} href={`/projects/${p.id}`}
                  className="flex items-center justify-between p-4 rounded-lg border border-border/50 hover:bg-muted/50 transition-colors">
                  <div>
                    <div className="font-medium">{p.name}</div>
                    <div className="text-sm text-muted-foreground">{p.candidate_count} candidates · {p.job_count} jobs</div>
                  </div>
                  <div className="flex items-center gap-3">
                    <Badge variant={p.status === 'completed' ? 'default' : 'secondary'}>{p.status}</Badge>
                    <ArrowRight className="w-4 h-4 text-muted-foreground" />
                  </div>
                </Link>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
