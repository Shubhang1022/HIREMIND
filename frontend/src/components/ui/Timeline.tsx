import { cn } from '@/lib/utils';

interface TimelineItem {
  title: string;
  company: string;
  startDate: string;
  endDate: string;
  type: 'promotion' | 'change';
}

interface TimelineProps {
  items: TimelineItem[];
}

export function Timeline({ items }: TimelineProps) {
  return (
    <div className="relative">
      <div className="absolute left-3 top-2 bottom-2 w-px bg-border" />
      <div className="space-y-6">
        {items.map((item, idx) => (
          <div key={idx} className="relative flex gap-4">
            <div className={cn(
              'mt-1 w-6 h-6 rounded-full border-2 flex items-center justify-center shrink-0 z-10',
              item.type === 'promotion' 
                ? 'bg-emerald-500/10 border-emerald-500' 
                : 'bg-blue-500/10 border-blue-500'
            )}>
              <div className={cn(
                'w-2 h-2 rounded-full',
                item.type === 'promotion' ? 'bg-emerald-500' : 'bg-blue-500'
              )} />
            </div>
            <div className="flex-1 pb-1">
              <div className="flex items-center justify-between">
                <h4 className="font-semibold">{item.title}</h4>
                <span className="text-sm text-muted-foreground">
                  {item.startDate} - {item.endDate}
                </span>
              </div>
              <p className="text-sm text-muted-foreground">{item.company}</p>
              <p className="text-xs mt-1">
                <span className={cn(
                  'inline-flex items-center px-2 py-0.5 rounded-full',
                  item.type === 'promotion'
                    ? 'bg-emerald-500/10 text-emerald-500'
                    : 'bg-blue-500/10 text-blue-500'
                )}>
                  {item.type === 'promotion' ? 'Promotion' : 'Company Change'}
                </span>
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
