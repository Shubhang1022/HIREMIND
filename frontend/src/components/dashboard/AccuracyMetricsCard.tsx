'use client';

import { Target } from 'lucide-react';
import type { AccuracyMetrics } from '@/lib/platform-api';

interface AccuracyMetricsCardProps {
  metrics: AccuracyMetrics;
  peakMemoryMb?: number;
}

export function AccuracyMetricsCard({ metrics, peakMemoryMb }: AccuracyMetricsCardProps) {
  return (
    <div className="bg-emerald-50/50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-500/25 rounded-xl p-4 text-sm">
      <div className="flex items-center gap-2 text-emerald-800 dark:text-emerald-300 font-semibold mb-3">
        <Target className="w-4 h-4 text-emerald-500 dark:text-emerald-400" />
        Analysis Accuracy Metrics
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3">
        <Metric label="Avg Match %" value={`${metrics.average_match_percent}%`} highlight />
        <Metric label="High Confidence" value={`${metrics.high_confidence_rate}%`} sub={`${metrics.high_confidence_count} candidates`} />
        <Metric label="Semantic Match" value={`${metrics.average_semantic_similarity_percent}%`} />
        <Metric label="Skill Match" value={`${metrics.average_skill_match_percent}%`} />
        <Metric label="Eligible Rate" value={`${metrics.eligible_rate}%`} />
        <Metric label="Role Match" value={`${metrics.average_role_match_percent}%`} />
        <Metric label="Experience Match" value={`${metrics.average_experience_match_percent}%`} />
        <Metric label="Avg AI Score" value={(metrics.average_ai_score * 100).toFixed(1)} />
        <Metric label="Ranked" value={String(metrics.ranked_count)} />
        {peakMemoryMb != null && peakMemoryMb > 0 && (
          <Metric label="Peak RAM" value={`${peakMemoryMb.toFixed(0)} MB`} />
        )}
      </div>
    </div>
  );
}

function Metric({ label, value, sub, highlight }: { label: string; value: string; sub?: string; highlight?: boolean }) {
  return (
    <div className="bg-emerald-50/80 dark:bg-emerald-950/30 p-2.5 rounded-lg border border-emerald-100 dark:border-emerald-500/10">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`text-lg font-bold ${highlight ? 'text-emerald-600 dark:text-emerald-400' : 'text-emerald-900 dark:text-emerald-200'}`}>
        {value}
      </p>
      {sub && <p className="text-[10px] text-muted-foreground mt-0.5">{sub}</p>}
    </div>
  );
}
