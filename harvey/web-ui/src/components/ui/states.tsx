import * as React from 'react'
import { AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'

export function EmptyState({ icon, title, children, className }: {
  icon?: React.ReactNode; title: React.ReactNode; children?: React.ReactNode; className?: string
}) {
  return (
    <div className={cn('flex flex-col items-center rounded-lg border border-dashed border-line-strong px-6 py-10 text-center', className)}>
      <div className="mb-2 text-text-3">{icon ?? <AlertTriangle className="size-5" />}</div>
      <div className="font-medium">{title}</div>
      {children && <div className="mt-1 max-w-[52ch] text-text-2">{children}</div>}
    </div>
  )
}

export function OfflineState() {
  return (
    <EmptyState title="Dashboard can't reach the server">
      The dashboard process may have stopped. Restart it with <b>harvey dashboard</b> and refresh this page.
    </EmptyState>
  )
}

/** Placeholder rows for the first fetch of a pane. */
export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-busy="true" className="space-y-2">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-10 animate-pulse rounded-md bg-surface-2" />
      ))}
    </div>
  )
}

export function Muted({ className, ...p }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn('text-[13px] text-text-2', className)} {...p} />
}

/** A banner for errors and warnings that must not read as "nothing to do". */
export function Banner({ tone = 'bad', children }: { tone?: 'bad' | 'wait' | 'good'; children: React.ReactNode }) {
  const t = { bad: 'bg-bad-bg text-bad border-bad-line', wait: 'bg-wait-bg text-wait border-wait-line', good: 'bg-good-bg text-good border-good-line' }[tone]
  return <div role="alert" className={cn('flex items-start gap-2 rounded-md border px-3 py-2 text-[13px]', t)}>{children}</div>
}
