import { useEffect, useRef, useState } from 'react'
import { post, downloadFile } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, Subhead } from '@/components/ui/card'
import { Muted, Banner } from '@/components/ui/states'
import { TableCard, TH, TD } from '../pipeline/shared'

export interface Signal {
  code: string; label?: string; description?: string; status: string
  cost_note?: string; companies?: number; confidence_floor?: number
}

interface CohortResult {
  error?: string; size: number; offset: number
  companies?: { name?: string; domain?: string; industry?: string; location?: string }[]
}

const PAGE = 50

function Column({ title, hint, signals, picked, onToggle }: {
  title: string; hint: string; signals: Signal[]; picked: Set<string>; onToggle: (code: string, on: boolean) => void
}) {
  return (
    <div>
      <Subhead hint={undefined}>{title} <span className="font-normal normal-case tracking-normal">{hint}</span></Subhead>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {signals.map(s => (
          <label key={s.code} className="flex cursor-pointer items-center gap-2 rounded-sm bg-surface-2 px-2.5 py-1.5 text-[13px]">
            <input type="checkbox" checked={picked.has(s.code)} onChange={e => onToggle(s.code, e.target.checked)} />
            <span className="flex-1">{s.label || s.code}</span>
            <span className="text-text-3 tabular-nums">{s.companies || 0}</span>
          </label>
        ))}
      </div>
    </div>
  )
}

export function CohortBuilder({ signals }: { signals: Signal[] }) {
  const confirmed = signals.filter(s => s.status === 'confirmed')
  const [req, setReq] = useState<Set<string>>(new Set())
  const [exc, setExc] = useState<Set<string>>(new Set())
  const [offset, setOffset] = useState(0)
  const [res, setRes] = useState<{ state: 'idle' | 'loading' | 'error' | 'ok'; data?: CohortResult; error?: string }>({ state: 'idle' })
  const [exporting, setExporting] = useState<'' | 'busy' | 'failed'>('')
  const gen = useRef(0)

  useEffect(() => {
    const generation = ++gen.current
    if (!req.size) { setRes({ state: 'idle' }); return }
    // Prior results (and their export button) go as soon as the criteria change.
    setRes({ state: 'loading' })
    post<CohortResult>('/api/cohort', { require: [...req], exclude: [...exc], offset, limit: PAGE }).then(data => {
      if (generation !== gen.current) return  // a newer query superseded this one
      if (!data || data.error) { setRes(data ? { state: 'error', error: data.error } : { state: 'idle' }); return }
      // The cohort shrank under us (a re-profile, a delete): back to page one.
      if (!data.companies?.length && data.size > 0 && offset > 0) { setOffset(0); return }
      setRes({ state: 'ok', data })
    })
  }, [req, exc, offset])

  const toggle = (key: 'require' | 'exclude', code: string, on: boolean) => {
    const [mine, setMine, other, setOther] = key === 'require' ? [req, setReq, exc, setExc] : [exc, setExc, req, setReq]
    const next = new Set(mine)
    if (on) next.add(code); else next.delete(code)
    setMine(next)
    // A company can't be both required and excluded on the same signal.
    if (on && other.has(code)) { const o = new Set(other); o.delete(code); setOther(o) }
    setOffset(0)  // a new cohort starts on its first page
  }

  const exportCsv = async () => {
    setExporting('busy')
    const qs = new URLSearchParams({ require: [...req].join(','), exclude: [...exc].join(',') })
    try { setExporting((await downloadFile('/api/cohort.csv?' + qs, 'cohort.csv')) ? '' : 'failed') } catch { setExporting('failed') }
  }

  return (
    <>
      <Subhead>Cohort builder</Subhead>
      <Card>
        <Muted className="mb-4">
          A prospect list is a query, not a file. Pick the signals a good prospect must have, and any that disqualify them, and see how many companies match right now.
        </Muted>
        {!confirmed.length ? (
          <Muted>Confirm some signals above and they become the building blocks here.</Muted>
        ) : (
          <>
            <Column title="Must have" hint="— every company in the cohort carries all of these" signals={confirmed} picked={req} onToggle={(c, on) => toggle('require', c, on)} />
            <div className="h-4" />
            <Column title="Must not have" hint="— disqualifiers" signals={confirmed} picked={exc} onToggle={(c, on) => toggle('exclude', c, on)} />
            <div aria-live="polite">
              {res.state === 'loading' && <Muted className="mt-5">Loading cohort…</Muted>}
              {res.state === 'error' && <div className="mt-5"><Banner>{res.error}</Banner></div>}
              {res.state === 'ok' && res.data && <Result d={res.data} offset={offset} setOffset={setOffset} exporting={exporting} onExport={exportCsv} />}
            </div>
          </>
        )}
      </Card>
    </>
  )
}

function Result({ d, offset, setOffset, exporting, onExport }: {
  d: CohortResult; offset: number; setOffset: (n: number) => void; exporting: '' | 'busy' | 'failed'; onExport: () => void
}) {
  const rows = d.companies || []
  const last = d.offset + rows.length
  return (
    <div className="mt-5 border-t border-line pt-5">
      <div className="text-[32px] font-semibold leading-none tabular-nums">{d.size}</div>
      <Muted className="mt-1">{d.size === 1 ? 'company matches' : 'companies match'} this cohort right now</Muted>
      {rows.length > 0 ? (
        <>
          <div className="mt-4">
            <TableCard>
              <table className="w-full text-[13px]">
                <thead><tr><th className={TH}>Company</th><th className={TH}>Domain</th><th className={TH}>Industry</th><th className={TH}>Location</th></tr></thead>
                <tbody>
                  {rows.map((c, i) => (
                    <tr key={i} className="border-t border-line">
                      <td className={TD}>{c.name}</td>
                      <td className={TD + ' text-text-2'}>{c.domain}</td>
                      <td className={TD + ' text-text-2'}>{c.industry}</td>
                      <td className={TD + ' text-text-2'}>{c.location}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableCard>
          </div>
          <div className="mt-2.5 flex items-center gap-2">
            <span className="flex-1 text-[12px] text-text-2">Showing {d.offset + 1}–{last} of {d.size}</span>
            <Button size="sm" disabled={d.offset <= 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Prev</Button>
            <Button size="sm" disabled={last >= d.size} onClick={() => setOffset(offset + PAGE)}>Next</Button>
            <Button size="sm" disabled={exporting === 'busy'} onClick={onExport}>
              {exporting === 'busy' ? 'Exporting…' : exporting === 'failed' ? 'Export failed — retry' : 'Export CSV'}
            </Button>
          </div>
        </>
      ) : d.size === 0 ? (
        <Muted className="mt-3">No company carries all of those yet. Either loosen the cohort, or run prospecting to collect more.</Muted>
      ) : null}
    </div>
  )
}
