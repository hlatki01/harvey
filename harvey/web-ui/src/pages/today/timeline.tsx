import { Activity, BarChart3, MessageSquare, Pencil, RefreshCw, Search, Send, User, type LucideIcon } from 'lucide-react'
import { actionText, own, relTime, type ActionRow } from '@/lib/format'
import { cn } from '@/lib/utils'

export interface ActivityRow extends ActionRow { agent?: string }

const AGENT_ICONS: Record<string, LucideIcon> = {
  scout: Search, writer: Pencil, sender: Send, handler: MessageSquare,
  analyst: BarChart3, user: User, main: RefreshCw,
}

/** One timeline row; `time` is already formatted (relative on Today, clock time on Activity). */
export function TimelineItem({ a, time, quiet }: { a: ActivityRow; time: string; quiet?: boolean }) {
  const Icon = own(AGENT_ICONS, String(a.agent)) ?? Activity
  return (
    <div className={cn('flex items-center gap-3 border-t border-line py-2 text-[13px] first:border-t-0', quiet && 'opacity-60')}>
      <span className="w-16 shrink-0 text-[12px] text-text-3">{time}</span>
      <Icon className="size-4 shrink-0 text-text-3" aria-hidden />
      <span className="min-w-0 flex-1 truncate" title={actionText(a)}>{actionText(a)}</span>
      <span className="shrink-0 text-[12px] text-text-3">{a.agent}</span>
    </div>
  )
}

export { relTime }
