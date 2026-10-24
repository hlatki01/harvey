import { cn } from '@/lib/utils'
import { statusMeta, type Tone } from '@/lib/format'

const TONES: Record<Tone, string> = {
  waiting: 'bg-wait-bg text-wait border-wait-line',
  active: 'bg-active-bg text-active border-active-line',
  good: 'bg-good-bg text-good border-good-line',
  bad: 'bg-bad-bg text-bad border-bad-line',
  note: 'bg-note-bg text-note border-note-line',
  idle: 'bg-surface-2 text-text-2 border-line',
}

export function ToneBadge({ tone, className, children }: { tone: Tone; className?: string; children: React.ReactNode }) {
  return (
    <span className={cn('inline-flex items-center rounded-xs border px-1.5 py-px text-[11px] font-medium leading-4', TONES[tone], className)}>
      {children}
    </span>
  )
}

/** A status from any Harvey table, in the one shared vocabulary. */
export function StatusBadge({ status, className }: { status: unknown; className?: string }) {
  const m = statusMeta(status)
  return <ToneBadge tone={m.tone} className={className}>{m.label}</ToneBadge>
}
