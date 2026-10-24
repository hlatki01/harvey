import { useState } from 'react'
import { useApi, useInvalidate } from '@/lib/hooks'
import { post } from '@/lib/api'
import { failMsg } from '@/lib/format'
import { useToast } from '@/components/toast'
import { Button } from '@/components/ui/button'
import { Card, SectionHead } from '@/components/ui/card'
import { StatusBadge } from '@/components/ui/badge'
import { OfflineState, Skeleton } from '@/components/ui/states'
import { CohortBuilder, type Signal } from './signals/Cohort'

interface Group { label: string; blurb?: string; signals: Signal[] }
interface SignalsData {
  error?: string; groups?: Group[]
  summary?: { confirmed?: number; proposed?: number; rejected?: number }
}

const chip = 'inline-flex items-center rounded-xs bg-surface-2 px-1.5 py-px text-[11px] text-text-2'

function SigRow({ sig, busy, onSet }: { sig: Signal; busy: boolean; onSet: (codes: string[], status: string) => void }) {
  const cost = sig.cost_note || ''
  const tone = /free|included/i.test(cost) ? 'text-good' : /\$/.test(cost) ? 'text-wait' : 'text-text-2'
  const n = sig.companies || 0
  return (
    <div className={'flex items-start justify-between gap-4 border-t border-line px-4 py-3 first:border-t-0' + (sig.status === 'rejected' ? ' opacity-60' : '')}>
      <div className="min-w-0">
        <div className="font-medium">{sig.label || sig.code} <span className="ml-1 font-mono text-[11px] font-normal text-text-3">{sig.code}</span></div>
        <div className="mt-0.5 text-[13px] text-text-2">{sig.description}</div>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span className={chip + ' ' + tone}>{cost || 'cost unknown'}</span>
          {(sig.confidence_floor ?? 0) > 0 && <span className={chip}>only recorded above {Math.round((sig.confidence_floor as number) * 100)}% confidence</span>}
          {n > 0 && <span className={chip}>seen on {n} {n === 1 ? 'company' : 'companies'} so far</span>}
          <StatusBadge status={sig.status} />
        </div>
      </div>
      <div className="flex shrink-0 gap-1.5">
        {sig.status === 'confirmed' ? (
          <Button size="sm" disabled={busy} onClick={() => onSet([sig.code], 'rejected')}>Turn off</Button>
        ) : sig.status === 'rejected' ? (
          <Button size="sm" disabled={busy} onClick={() => onSet([sig.code], 'confirmed')}>Turn on</Button>
        ) : (
          <>
            <Button size="sm" variant="primary" disabled={busy} onClick={() => onSet([sig.code], 'confirmed')}>Confirm</Button>
            <Button size="sm" disabled={busy} onClick={() => onSet([sig.code], 'rejected')}>Skip</Button>
          </>
        )}
      </div>
    </div>
  )
}

export function SignalsPage() {
  const { data, refetch } = useApi<SignalsData>('/api/signals')
  const invalidate = useInvalidate()
  const toast = useToast()
  const [busy, setBusy] = useState(false)

  const setSignals = async (codes: string[], status: string) => {
    setBusy(true)
    const res = await post<{ success?: boolean; changed: number }>('/api/signals/status', { codes, status })
    setBusy(false)
    if (res && res.success) {
      const s = res.changed === 1 ? '' : 's'
      toast(status === 'confirmed'
        ? `Confirmed ${res.changed} signal${s} — Harvey will collect ${res.changed === 1 ? 'it' : 'them'} from now on.`
        : `${res.changed} signal${s} turned off.`, 'success')
    } else {
      toast(failMsg(res, 'Could not update that signal.'), 'error')
    }
    refetch()
    invalidate('/api/today')
  }

  const head = (
    <SectionHead title="Signals">
      Harvey proposes what it knows how to look for. You decide what actually defines a good prospect; nothing is collected until you confirm it.
    </SectionHead>
  )
  if (data === undefined) return <>{head}<Skeleton /></>
  if (!data || data.error) return <>{head}<OfflineState /></>

  const s = data.summary || {}
  const parts: [string, number, string][] = [
    ['Confirmed', s.confirmed || 0, 'bg-good'], ['Waiting on you', s.proposed || 0, 'bg-wait'], ['Turned off', s.rejected || 0, 'bg-text-3'],
  ]
  const all = parts.reduce((x, p) => x + p[1], 0) || 1
  const groups = data.groups || []

  return (
    <>
      {head}
      <Card className="mb-6">
        <div className="flex h-2 gap-0.5 overflow-hidden rounded-full bg-surface-2">
          {parts.filter(p => p[1]).map(p => <span key={p[0]} className={p[2]} style={{ flex: p[1] }} />)}
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {parts.map(p => (
            <div key={p[0]} className="flex items-baseline justify-between text-[13px]">
              <span className="flex items-center gap-1.5 text-text-2"><i className={'size-2 rounded-full ' + p[2]} />{p[0]}</span>
              <span className="font-medium tabular-nums">{p[1]} <small className="font-normal text-text-3">{Math.round(100 * p[1] / all)}%</small></span>
            </div>
          ))}
        </div>
      </Card>

      {groups.map(g => {
        // Only the undecided ones: "Confirm all" must never re-enable a signal
        // you turned off (or "Skip all" turn off one you confirmed).
        const pending = g.signals.filter(x => x.status === 'proposed').map(x => x.code)
        return (
          <section key={g.label} className="mb-6">
            <div className="mb-2 flex items-start justify-between gap-4">
              <div>
                <h3 className="font-semibold">{g.label}</h3>
                <p className="text-[13px] text-text-2">{g.blurb}</p>
              </div>
              {pending.length > 0 && (
                <div className="flex shrink-0 gap-1.5">
                  <Button size="sm" variant="primary" disabled={busy} onClick={() => setSignals(pending, 'confirmed')}>Confirm all {pending.length}</Button>
                  <Button size="sm" disabled={busy} onClick={() => setSignals(pending, 'rejected')}>Skip all {pending.length}</Button>
                </div>
              )}
            </div>
            <Card flush>{g.signals.map(sig => <SigRow key={sig.code} sig={sig} busy={busy} onSet={setSignals} />)}</Card>
          </section>
        )
      })}

      <CohortBuilder signals={groups.flatMap(g => g.signals)} />
    </>
  )
}
