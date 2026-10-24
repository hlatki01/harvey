import { Check } from 'lucide-react'
import { Card, Subhead } from '@/components/ui/card'
import { StatusBadge } from '@/components/ui/badge'
import { Muted } from '@/components/ui/states'
import { relTime } from '@/lib/format'
import { useApi } from '@/lib/hooks'
import { cn } from '@/lib/utils'

interface StatsData { error?: string; prospects?: { total?: number }; pipeline?: Record<string, number> }

const STAGES: [string, string, string][] = [
  ['to_write', 'To write', 'bg-accent-soft'], ['contacted', 'Contacted', 'bg-accent'],
  ['replied', 'Replied', 'bg-active'], ['interested', 'Interested', 'bg-good'],
  ['ready_to_meet', 'Ready to meet', 'bg-note'], ['closed_out', 'Closed out', 'bg-text-3'],
]

/** Contacts by stage: one bar, one legend, from /api/stats. */
export function Funnel() {
  const { data } = useApi<StatsData>('/api/stats', { interval: 15000 })
  if (!data) return null  // offline: keep nothing; Today's queue says so
  if (data.error) return <Muted>Couldn't read the pipeline.</Muted>
  const total = data.prospects?.total || 0
  if (!total) return <Muted>No contacts yet. Scout adds them as it finds people.</Muted>
  const stages = STAGES.map(([k, label, color]) => ({ label, color, n: data.pipeline?.[k] || 0 }))
  return (
    <>
      <div className="mb-3 flex h-2.5 overflow-hidden rounded-full bg-surface-3" role="img" aria-label="Contacts by stage">
        {stages.filter(s => s.n > 0).map(s => (
          <span key={s.label} className={s.color} style={{ flex: s.n }} title={`${s.label}: ${s.n}`} />
        ))}
      </div>
      {stages.map(s => (
        <div key={s.label} className="flex items-center gap-2 py-0.5 text-[13px]">
          <i className={cn('size-2 rounded-full', s.color)} />
          <span className="flex-1 text-text-2">{s.label}</span>
          <span className="tabular-nums">{s.n}</span>
          <span className="w-10 text-right tabular-nums text-text-3">{Math.round(100 * s.n / total)}%</span>
        </div>
      ))}
    </>
  )
}

interface Run { stage?: string; status?: string; started_at?: string; records?: number; cost_usd?: number }

export function Runs() {
  const { data } = useApi<Run[]>('/api/runs', { interval: 15000 })
  if (!Array.isArray(data) || !data.length) return null
  return (
    <>
      <Subhead>Collector runs</Subhead>
      <Card className="py-2">
        {data.slice(0, 5).map((r, i) => (
          <div key={i} className="flex items-center gap-2 border-t border-line py-2 text-[13px] first:border-t-0">
            <span className="font-medium">{r.stage}</span><StatusBadge status={r.status} />
            <span className="text-text-3">{relTime(r.started_at)}</span>
            <span className="ml-auto text-text-2">{r.records || 0} records · {r.cost_usd ? '$' + Number(r.cost_usd).toFixed(4) : 'free'}</span>
          </div>
        ))}
      </Card>
    </>
  )
}

interface Check { label?: string; help?: string; done?: boolean; required?: boolean }

function CheckRow({ c, optional }: { c: Check; optional?: boolean }) {
  return (
    <div className="flex items-start gap-2 py-1.5">
      <span className={cn('mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border', c.done ? 'border-good-line bg-good-bg text-good' : 'border-line-strong')}>
        {c.done && <Check className="size-3" aria-hidden />}
      </span>
      <div className="text-[13px]">
        <div className={cn(c.done && 'text-text-3 line-through')}>
          {c.label}{optional && <span className="ml-2 text-[11px] text-text-3">optional</span>}
        </div>
        {!c.done && <div className="text-text-2">{c.help}</div>}
      </div>
    </div>
  )
}

/** Setup checklist; finished setup disappears completely rather than greeting you forever. */
export function Setup() {
  const { data } = useApi<{ checks?: Check[]; percent?: number }>('/api/setup-status', { interval: 15000 })
  if (!data || !data.checks) return null
  const pct = data.percent || 0
  if (pct === 100) return null
  const required = data.checks.filter(c => c.required)
  const optional = data.checks.filter(c => !c.required)
  return (
    <>
      <Subhead hint={`${required.filter(c => c.done).length} of ${required.length} done`}>Setup</Subhead>
      <Card>
        <div className="mb-2 h-2 overflow-hidden rounded-full bg-surface-3"><div className="h-full bg-accent" style={{ width: pct + '%' }} /></div>
        {required.map((c, i) => <CheckRow key={i} c={c} />)}
        {optional.length > 0 && (
          <details className="mt-2">
            <summary className="cursor-pointer text-[12px] text-text-2">{optional.length} optional</summary>
            <div className="mt-1.5">{optional.map((c, i) => <CheckRow key={i} c={c} optional />)}</div>
          </details>
        )}
      </Card>
    </>
  )
}
