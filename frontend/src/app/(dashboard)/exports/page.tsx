'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { LoadingState } from '@/components/ui/LoadingState';
import { useDemoCandidates } from '@/lib/useDemoData';
import { exportToCsv, exportToJson } from '@/lib/export';
import { Download, FileText, Users, TrendingUp, Clock, CheckCircle2 } from 'lucide-react';

type ExportKind = 'csv' | 'json' | 'insights';

interface HistoryItem {
  id: number;
  name: string;
  date: string;
  status: 'completed' | 'processing';
  size: string;
}

export default function ExportsPage() {
  const { candidates, loading } = useDemoCandidates();
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [busy, setBusy] = useState<ExportKind | null>(null);

  const runExport = async (kind: ExportKind, label: string) => {
    setBusy(kind);
    try {
      await new Promise((r) => setTimeout(r, 400));
      if (kind === 'csv') {
        exportToCsv(
          candidates.map((c) => ({
            rank: c.rank,
            name: c.name,
            role: c.role,
            aiScore: c.aiScore,
            matchPercent: c.matchPercent,
            integrityScore: c.integrityScore,
            reasoning: c.summary,
          })),
          `ranked-candidates-${Date.now()}.csv`
        );
      } else if (kind === 'json') {
        exportToJson(candidates, `candidate-insights-${Date.now()}.json`);
      } else {
        exportToJson(
          {
            job: 'Senior AI Engineer — Redrob AI',
            exportedAt: new Date().toISOString(),
            topCandidates: candidates,
          },
          `recruiter-insights-${Date.now()}.json`
        );
      }
      setHistory((prev) => [
        {
          id: Date.now(),
          name: label,
          date: new Date().toLocaleString(),
          status: 'completed',
          size: kind === 'csv' ? '~12 KB' : '~28 KB',
        },
        ...prev,
      ]);
      toast.success(`${label} downloaded`);
    } catch {
      toast.error('Export failed');
    } finally {
      setBusy(null);
    }
  };

  const exportTypes = [
    {
      key: 'csv' as const,
      title: 'Ranked Candidate CSV',
      description: 'Contest-format export: rank, score, reasoning',
      icon: Users,
      color: 'text-blue-500',
    },
    {
      key: 'json' as const,
      title: 'Candidate Insights JSON',
      description: 'Full profiles with explainability fields',
      icon: TrendingUp,
      color: 'text-emerald-500',
    },
    {
      key: 'insights' as const,
      title: 'Recruiter Insights JSON',
      description: 'Job context + ranked candidates for stakeholders',
      icon: FileText,
      color: 'text-purple-500',
    },
  ];

  return (
    <div className="p-4 lg:p-8 pt-20 lg:pt-24 space-y-6">
      <div>
        <h1 className="text-2xl lg:text-3xl font-bold">Exports</h1>
        <p className="text-muted-foreground mt-1">Download ranked lists and recruiter-ready reports</p>
      </div>

      {loading ? (
        <LoadingState rows={3} />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {exportTypes.map((exp) => {
            const Icon = exp.icon;
            return (
              <Card key={exp.key} className="hover:shadow-lg transition-shadow">
                <CardContent className="pt-6">
                  <Icon className={`w-8 h-8 mb-3 ${exp.color}`} />
                  <h3 className="font-semibold mb-1">{exp.title}</h3>
                  <p className="text-sm text-muted-foreground mb-4">{exp.description}</p>
                  <Button
                    className="w-full"
                    disabled={busy !== null}
                    onClick={() => runExport(exp.key, exp.title)}
                  >
                    <Download className="w-4 h-4 mr-2" />
                    {busy === exp.key ? 'Generating…' : 'Download'}
                  </Button>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Export History</CardTitle>
        </CardHeader>
        <CardContent>
          {history.length === 0 ? (
            <p className="text-sm text-muted-foreground py-6 text-center">
              Exports from this session will appear here.
            </p>
          ) : (
            <div className="space-y-3">
              {history.map((item) => (
                <div key={item.id} className="flex items-center justify-between p-4 rounded-lg border bg-card">
                  <div className="flex items-center gap-4">
                    <div className="p-2 rounded-lg bg-muted">
                      <FileText className="w-5 h-5 text-muted-foreground" />
                    </div>
                    <div>
                      <p className="font-medium">{item.name}</p>
                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Clock className="w-4 h-4" />
                        {item.date}
                        <span>•</span>
                        {item.size}
                      </div>
                    </div>
                  </div>
                  <Badge variant="default" className="gap-1">
                    <CheckCircle2 className="w-3 h-3" />
                    {item.status}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
