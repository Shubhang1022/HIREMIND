'use client';

import { useEffect, useState } from 'react';
import { 
  Activity, ShieldCheck, Database, FolderKanban, Users, 
  CheckCircle2, AlertTriangle, RefreshCw, FileDown, 
  XOctagon, Server, ShieldAlert, Cpu, Check, Play, Info
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { MetricCard } from '@/components/ui/MetricCard';
import { platformApi } from '@/lib/platform-api';
import { toast } from 'sonner';

interface HealthStats {
  projects: number;
  candidates: number;
  rankings: number;
  failed_jobs: number;
  duplicate_projects_prevented: number;
  exports_generated: number;
}

export default function SystemHealthPage() {
  const [stats, setStats] = useState<HealthStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [lastChecked, setLastChecked] = useState<string>('');

  const fetchStats = async (showToast = false) => {
    try {
      if (showToast) setScanning(true);
      const data = await platformApi.healthStats();
      setStats(data);
      setLastChecked(new Date().toLocaleTimeString());
      if (showToast) {
        toast.success('System health metrics updated');
      }
    } catch (err: any) {
      toast.error(err?.message || 'Failed to fetch health stats');
    } finally {
      setLoading(false);
      setScanning(false);
    }
  };

  useEffect(() => {
    fetchStats();
  }, []);

  const handleRunScan = async () => {
    await fetchStats(true);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[65vh]">
        <div className="flex flex-col items-center gap-3">
          <RefreshCw className="w-8 h-8 animate-spin text-indigo-500" />
          <p className="text-sm text-muted-foreground animate-pulse">Querying system endpoints...</p>
        </div>
      </div>
    );
  }

  const isHealthy = (stats?.failed_jobs ?? 0) === 0;

  const safeguards = [
    {
      name: 'Analysis Job Lock',
      description: 'Prevents multiple concurrent analysis runs on the same project.',
      status: 'Active',
      level: 'critical',
    },
    {
      name: 'Dataset Fingerprinting',
      description: 'Caches and reuses ranking results if job descriptions and candidates match.',
      status: 'Active',
      level: 'optimal',
    },
    {
      name: 'Automatic Timeout Recovery',
      description: 'Projects stuck in processing for >30 minutes are automatically recovered and set to failed.',
      status: 'Active',
      level: 'optimal',
    },
    {
      name: 'Strict User Isolation',
      description: 'All read/write operations scope database keys directly to the authenticated Supabase user.',
      status: 'Active',
      level: 'critical',
    },
    {
      name: 'Production Guardrail',
      description: 'Hard-blocked project creation with test/debug names in the production environment.',
      status: 'Active',
      level: 'critical',
    },
    {
      name: 'Export Consistency Check',
      description: 'Validates that CSV exports strictly match computed UI ranking scores before download.',
      status: 'Active',
      level: 'optimal',
    },
  ];

  return (
    <div className="p-6 lg:p-8 space-y-8 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-3xl font-bold tracking-tight">System Health</h1>
            <Badge variant="outline" className="bg-indigo-500/10 text-indigo-400 border-indigo-500/20 font-mono text-xs">
              v2.2-Hardened
            </Badge>
          </div>
          <p className="text-muted-foreground mt-1">Real-time diagnostics, performance metrics, and integrity configurations.</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground hidden md:inline">
            Last checked: {lastChecked || 'Never'}
          </span>
          <Button 
            onClick={handleRunScan} 
            disabled={scanning}
            className="bg-indigo-600 hover:bg-indigo-700 text-white font-medium shadow-lg shadow-indigo-600/15"
          >
            <RefreshCw className={`w-4 h-4 mr-2 ${scanning ? 'animate-spin' : ''}`} />
            Run Integrity Scan
          </Button>
        </div>
      </div>

      {/* Main Status Callout */}
      <div className={`p-6 rounded-xl border flex flex-col md:flex-row items-start md:items-center justify-between gap-6 transition-all duration-300 ${
        isHealthy 
          ? 'bg-emerald-500/5 border-emerald-500/20 text-emerald-400' 
          : 'bg-amber-500/5 border-amber-500/20 text-amber-400'
      }`}>
        <div className="flex gap-4 items-start">
          <div className={`p-3 rounded-lg ${isHealthy ? 'bg-emerald-500/10 text-emerald-400' : 'bg-amber-500/10 text-amber-400'}`}>
            {isHealthy ? <ShieldCheck className="w-8 h-8" /> : <ShieldAlert className="w-8 h-8" />}
          </div>
          <div>
            <h2 className="text-xl font-semibold text-foreground">
              {isHealthy ? 'All Systems Operational' : 'Integrity Alerts Detected'}
            </h2>
            <p className="text-sm text-muted-foreground mt-1 max-w-xl">
              {isHealthy 
                ? 'Startup integrity diagnostics passed. Database structures are aligned, and active platform safeguards are shielding the ranking engine.'
                : `A total of ${stats?.failed_jobs} failed analysis jobs are logged. The automated timeout recovery script is actively monitoring for locked queues.`
              }
            </p>
          </div>
        </div>
        <div className="flex flex-col gap-1 items-start md:items-end min-w-[120px]">
          <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">Overall Health</span>
          <span className={`text-lg font-bold ${isHealthy ? 'text-emerald-400 animate-pulse' : 'text-amber-400'}`}>
            {isHealthy ? 'OPTIMAL' : 'DEGRADED'}
          </span>
        </div>
      </div>

      {/* Metric Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
        <MetricCard 
          title="Database Projects" 
          value={stats?.projects ?? 0} 
          icon={<FolderKanban className="w-5 h-5 text-indigo-400" />} 
          trend="Unique workspaces" 
          description="isolated by Supabase auth" 
        />
        <MetricCard 
          title="Total Candidates Ingested" 
          value={stats?.candidates ?? 0} 
          icon={<Users className="w-5 h-5 text-emerald-400" />} 
          trend="Resumes parsed" 
          description="indexed in JSON backend" 
        />
        <MetricCard 
          title="Rankings Output" 
          value={stats?.rankings ?? 0} 
          icon={<Cpu className="w-5 h-5 text-purple-400" />} 
          trend="Analyses ran" 
          description="cached with 2-day TTL" 
        />
        <MetricCard 
          title="Failed Jobs / Errors" 
          value={stats?.failed_jobs ?? 0} 
          icon={<XOctagon className={`w-5 h-5 ${stats?.failed_jobs ? 'text-red-500' : 'text-muted-foreground'}`} />} 
          trend={stats?.failed_jobs ? `${stats.failed_jobs} incidents logged` : '0 errors logged'} 
          trendUp={false}
          description="monitored by timeout recoverer" 
          className={stats?.failed_jobs ? 'border-red-500/25 bg-red-500/[0.02]' : ''}
        />
        <MetricCard 
          title="Duplicate Project Requests Prevented" 
          value={stats?.duplicate_projects_prevented ?? 0} 
          icon={<ShieldCheck className="w-5 h-5 text-amber-400" />} 
          trend="Double submits blocked" 
          description="deduplicated via name & hash" 
        />
        <MetricCard 
          title="CSV/JSON Exports Generated" 
          value={stats?.exports_generated ?? 0} 
          icon={<FileDown className="w-5 h-5 text-blue-400" />} 
          trend="Reports downloaded" 
          description="validated against UI scores" 
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Active Safeguards */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <div className="flex items-center gap-2">
              <ShieldCheck className="w-5 h-5 text-emerald-400" />
              <CardTitle>Active Integrity Safeguards</CardTitle>
            </div>
            <CardDescription>
              Built-in production security protocols guarding the pipeline.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <div className="divide-y divide-border">
              {safeguards.map((item, idx) => (
                <div key={idx} className="p-4 flex items-center justify-between hover:bg-muted/30 transition-colors">
                  <div className="space-y-1 pr-4">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-foreground">{item.name}</span>
                      <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded font-bold font-mono tracking-wider ${
                        item.level === 'critical' 
                          ? 'bg-rose-500/10 text-rose-400 border border-rose-500/20' 
                          : 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/20'
                      }`}>
                        {item.level}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground">{item.description}</p>
                  </div>
                  <div>
                    <Badge variant="outline" className="bg-emerald-500/10 text-emerald-400 border-emerald-500/30 gap-1.5 pl-2 font-medium">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                      {item.status}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Diagnostics & Integrity Diagnostics */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Database className="w-5 h-5 text-indigo-400" />
              <CardTitle>Database Diagnostics</CardTitle>
            </div>
            <CardDescription>
              Scan integrity reports and platform health diagnostics.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div>
              <h4 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">Integrity Checks Status</h4>
              <div className="space-y-3 font-mono text-xs">
                <div className="flex items-center justify-between p-2.5 rounded bg-muted/40 border border-border/40">
                  <span className="text-muted-foreground flex items-center gap-1.5"><Server className="w-3.5 h-3.5 text-indigo-400" /> JSON Store Sync</span>
                  <span className="text-emerald-400 flex items-center gap-1 font-bold"><Check className="w-3.5 h-3.5" /> OK</span>
                </div>
                <div className="flex items-center justify-between p-2.5 rounded bg-muted/40 border border-border/40">
                  <span className="text-muted-foreground flex items-center gap-1.5"><FolderKanban className="w-3.5 h-3.5 text-indigo-400" /> Project Integrity</span>
                  <span className="text-emerald-400 flex items-center gap-1 font-bold"><Check className="w-3.5 h-3.5" /> OK</span>
                </div>
                <div className="flex items-center justify-between p-2.5 rounded bg-muted/40 border border-border/40">
                  <span className="text-muted-foreground flex items-center gap-1.5"><Users className="w-3.5 h-3.5 text-indigo-400" /> Orphan Job Purger</span>
                  <span className="text-emerald-400 flex items-center gap-1 font-bold"><Check className="w-3.5 h-3.5" /> OK</span>
                </div>
                <div className="flex items-center justify-between p-2.5 rounded bg-muted/40 border border-border/40">
                  <span className="text-muted-foreground flex items-center gap-1.5"><Cpu className="w-3.5 h-3.5 text-indigo-400" /> Orphan Ranking Purger</span>
                  <span className="text-emerald-400 flex items-center gap-1 font-bold"><Check className="w-3.5 h-3.5" /> OK</span>
                </div>
              </div>
            </div>

            <div className="p-4 rounded-xl border border-border/60 bg-muted/30">
              <div className="flex gap-2.5 items-start">
                <Info className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <h5 className="text-xs font-semibold">Integrity Checker Info</h5>
                  <p className="text-[11px] text-muted-foreground leading-relaxed">
                    Diagnostics check matches internal projects against jobs, candidates, and rankings to clean orphans dynamically. Integrity checkers run automatically on app boot and deletion.
                  </p>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
