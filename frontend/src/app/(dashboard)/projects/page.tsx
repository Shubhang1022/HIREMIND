'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Plus, FolderKanban, Loader2, MoreHorizontal, Trash2 } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { platformApi, type Project } from '@/lib/platform-api';
import { toast } from 'sonner';

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    platformApi.projects.list()
      .then(setProjects)
      .catch(() => toast.error('Failed to load projects'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this project and all its data?')) return;
    try {
      await platformApi.projects.delete(id);
      toast.success('Project deleted');
      load();
    } catch {
      toast.error('Failed to delete project');
    }
  };

  return (
    <div className="p-6 lg:p-8 space-y-8">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold">Projects</h1>
          <p className="text-muted-foreground mt-1">Manage your hiring projects</p>
        </div>
        <Link href="/projects/new">
          <Button className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white border-0">
            <Plus className="w-4 h-4 mr-2" /> New Project
          </Button>
        </Link>
      </div>

      {loading ? (
        <div className="flex justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-indigo-400" /></div>
      ) : projects.length === 0 ? (
        <Card className="border-dashed"><CardContent className="py-16 text-center">
          <FolderKanban className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
          <p className="text-muted-foreground mb-4">No projects yet. Create one to get started.</p>
          <Link href="/projects/new"><Button><Plus className="w-4 h-4 mr-2" /> Create Project</Button></Link>
        </CardContent></Card>
      ) : (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects.map(p => (
            <Card key={p.id} className="group hover:border-indigo-500/30 transition-colors">
              <CardContent className="p-6">
                <div className="flex justify-between items-start mb-4">
                  <Link href={`/projects/${p.id}`} className="flex-1">
                    <h3 className="font-semibold text-lg group-hover:text-indigo-400 transition-colors">{p.name}</h3>
                  </Link>
                  <DropdownMenu>
                    <DropdownMenuTrigger render={<Button variant="ghost" size="icon" className="h-8 w-8"><MoreHorizontal className="w-4 h-4" /></Button>} />
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem className="text-destructive" onClick={() => handleDelete(p.id)}>
                        <Trash2 className="w-4 h-4 mr-2" />Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
                {p.description && <p className="text-sm text-muted-foreground mb-4 line-clamp-2">{p.description}</p>}
                <div className="flex items-center justify-between">
                  <div className="text-sm text-muted-foreground">{p.candidate_count} candidates · {p.job_count} jobs</div>
                  <Badge variant={p.status === 'completed' ? 'default' : 'secondary'}>{p.status}</Badge>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
