import { useRef } from 'react'
import { AlertTriangle, ArrowRight, Check } from 'lucide-react'
import { Banner, Muted } from '@/components/ui/states'
import { Button } from '@/components/ui/button'
import { own } from '@/lib/format'
import { useApi } from '@/lib/hooks'
import { useNav, type TabId } from '@/app/nav'
import { cn } from '@/lib/utils'

interface Step { label?: string; detail?: string; state?: string; done?: number; total?: number }
interface Job { stage?: string; provider?: string; elapsed_min?: number; eta_text?: string }
interface Live { steps: Step[]; running?: Job[]; stopped?: Job[]; cost_usd?: number; next?: string; next_tab?: string }

const FILL: Record<string, string> = { done: 'bg-good', running: 'bg-accent', partial: 'bg-accent', waiting: 'bg-line-strong' }

const jobName = (r: Job) => r.stage === 'profile' ? 'Reading websites'
  : r.stage === 'discover' ? 'Searching for businesses (' + (r.provider || '') + ')'
  : String(r.stage || 'Job')

const Pulse = () => <span className="inline-block size-2 animate-pulse rounded-full bg-accent" aria-hidden />

export function LiveStatus() {
  const { data } = useApi<Live>('/api/live-status', { interval: 10000 })
  const { goTab } = useNav()
  // Keep the last good numbers on screen, but never let them pass for live.
  const last = useRef<{ d: Live; at: Date } | null>(null)
  if (data && Array.isArray(data.steps)) last.current = { d: data, at: new Date() }
  const stale = data !== undefined && !(data && Array.isArray(data.steps))
  const cur = last.current
  if (!cur) {
    return stale
      ? <Muted>Can't reach Harvey right now. Is the dashboard window still open?</Muted>
      : <div aria-busy="true" className="h-24 animate-pulse rounded-md bg-surface-2" />
  }
  const d = cur.d
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-3">
        {(d.running || []).length
          ? (d.running || []).map((r, i) => (
              <span key={i} className="inline-flex items-center gap-1.5 text-[13px] font-medium">
                <Pulse />{jobName(r)} · {r.elapsed_min} min{r.eta_text ? ' · ' + r.eta_text : ''}
              </span>))
          : <span className="text-[13px] font-medium">Nothing running right now</span>}
        <span className="ml-auto text-[12px] text-text-3">Cost so far ${Number(d.cost_usd || 0).toFixed(2)}</span>
      </div>
      {(d.stopped || []).length > 0 && (
        <div className="mb-2">
          <Banner><AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <p>{(d.stopped || []).map(jobName).join(', ')} stopped before finishing.</p></Banner>
        </div>
      )}
      <div>
        {d.steps.map((s, i) => {
          const st = own(FILL, String(s.state)) ? String(s.state) : 'waiting'
          const pct = s.total ? Math.min(100, Math.round(100 * (s.done || 0) / s.total)) : 0
          return (
            <div key={i} className="flex items-center gap-3 border-t border-line py-2 text-[13px] first:border-t-0">
              <span className={cn('flex size-5 shrink-0 items-center justify-center rounded-full', st === 'done' ? 'bg-good-bg text-good' : 'bg-surface-2')}>
                {st === 'done' ? <Check className="size-3" aria-hidden /> : st === 'running' ? <Pulse /> : null}
              </span>
              <span className="font-medium">{s.label}</span>
              <span className="min-w-0 flex-1 truncate text-text-2">{s.detail}</span>
              {s.total ? (
                <span className="h-1.5 w-24 shrink-0 overflow-hidden rounded-full bg-surface-3" aria-hidden>
                  <span className={cn('block h-full', FILL[st])} style={{ width: pct + '%' }} />
                </span>
              ) : null}
            </div>
          )
        })}
      </div>
      {d.next && (
        <div className="mt-2 flex items-center gap-2 border-t border-line pt-2 text-[13px]">
          <ArrowRight className="size-4 text-text-3" aria-hidden />
          <span className="flex-1"><b>Next</b> · {d.next}</span>
          {d.next_tab && <Button size="sm" onClick={() => goTab(d.next_tab as TabId)}>Go</Button>}
        </div>
      )}
      <div className={cn('mt-2 text-[12px]', stale ? 'text-bad' : 'text-text-3')} role={stale ? 'alert' : undefined}>
        {stale
          ? "Can't reach Harvey right now — numbers below may be old."
          : `Updated ${cur.at.toLocaleTimeString()} · refreshes every 10 seconds`}
      </div>
    </div>
  )
}
