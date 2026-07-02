import { cn } from '@/lib/utils';

interface ScoreBadgeProps {
  score: number;
  type?: 'primary' | 'success' | 'warning' | 'danger';
  size?: 'sm' | 'md' | 'lg';
}

export function ScoreBadge({ score, type, size = 'md' }: ScoreBadgeProps) {
  const getType = (score: number) => {
    if (score >= 90) return 'success';
    if (score >= 75) return 'primary';
    if (score >= 60) return 'warning';
    return 'danger';
  };

  const badgeType = type || getType(score);

  const sizeClasses = {
    sm: 'text-xs px-2 py-1',
    md: 'text-sm px-3 py-1.5',
    lg: 'text-base px-4 py-2',
  };

  const typeClasses = {
    success: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20',
    primary: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
    warning: 'bg-amber-500/10 text-amber-500 border-amber-500/20',
    danger: 'bg-red-500/10 text-red-500 border-red-500/20',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center font-semibold rounded-full border',
        sizeClasses[size],
        typeClasses[badgeType]
      )}
    >
      {score}
    </span>
  );
}
