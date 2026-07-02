import { cn } from '@/lib/utils';

interface MatchStatusBadgeProps {
  status: 'excellent' | 'strong' | 'moderate' | 'weak';
}

export function MatchStatusBadge({ status }: MatchStatusBadgeProps) {
  const styles = {
    excellent: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20',
    strong: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
    moderate: 'bg-amber-500/10 text-amber-500 border-amber-500/20',
    weak: 'bg-red-500/10 text-red-500 border-red-500/20',
  };

  const labels = {
    excellent: 'Excellent Match',
    strong: 'Strong Match',
    moderate: 'Moderate Match',
    weak: 'Weak Match',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center text-xs font-semibold px-2.5 py-0.5 rounded-full border',
        styles[status]
      )}
    >
      {labels[status]}
    </span>
  );
}
