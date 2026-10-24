import { useEffect, useRef, useState } from 'react'
import { useApi, useInvalidate } from '@/lib/hooks'
import { post } from '@/lib/api'
import { failMsg, safeHref } from '@/lib/format'
import { useToast } from '@/components/toast'
import { Button } from '@/components/ui/button'
import { Card, SectionHead, Subhead } from '@/components/ui/card'
import { ToneBadge } from '@/components/ui/badge'
import { Input, Field } from '@/components/ui/field'
import { Banner, Muted, OfflineState, Skeleton } from '@/components/ui/states'
import { cn } from '@/lib/utils'

interface Provider {
  key: string; label: string; blurb: string; cost_note: string; free_tier: string
  configured?: boolean; needs_key?: boolean; env_keys: string[]; signup_url?: string; caveat?: string
}
interface Report {
  new_companies?: number; known_companies?: number; observations?: number; junk?: number
  profiled_companies?: number; profile_observations?: number; actual_cost?: number
  stopped?: string; errors?: string[]
}
interface Providers {
  providers?: Provider[]; selected?: string; default?: string; running?: boolean
  running_kind?: string; unprofiled?: number; last_report?: Report | null
}
interface Estimate { error?: string; estimated_cost: number; free: boolean; query_count: number; queries: string[] }

function Stat({ label, value, accent }: { label: string; value: React.ReactNode; accent?: boolean }) {
  return (
    <Card>
      <div className="text-[12px] text-text-3">{label}</div>
      <div className={cn('mt-1 text-[22px] font-semibold tabular-nums', accent && 'text-accent')}>{value}</div>
    </Card>
  )
}

function Result({ report, running }: { report?: Report | null; running?: boolean }) {
  if (running) {
    return (
      <Card className="mt-6">
        <h3 className="font-semibold">Running…</h3>
        <Muted className="mt-1">Harvey is working through the queries. Results land in Companies, and the run log is on Today.</Muted>
      </Card>
    )
  }
  if (!report) return null
  const stopped = report.stopped && <div className="mt-3.5"><Banner>Stopped early: {report.stopped}</Banner></div>
  // A profile-only run has no discovery counters.
  if (report.profiled_companies !== undefined && report.new_companies === undefined) {
    return (
      <>
        <Subhead>Last run</Subhead>
        <div className="grid gap-3 sm:grid-cols-3">
          <Stat label="Websites read" value={report.profiled_companies || 0} accent />
          <Stat label="Facts recorded" value={report.profile_observations || 0} />
          <Stat label="Cost" value="free" />
        </div>
        {stopped}
      </>
    )
  }
  const errors = (report.errors || []).slice(0, 8)
  return (
    <>
      <Subhead>Last run</Subhead>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="New companies" value={report.new_companies || 0} accent />
        <Stat label="Already known" value={report.known_companies || 0} />
        <Stat label="Observations" value={report.observations || 0} />
        <Stat label="Filtered as junk" value={report.junk || 0} />
        {report.profiled_companies ? <Stat label="Websites read" value={report.profiled_companies} /> : null}
        <Stat label="Actual cost" value={report.actual_cost ? '$' + report.actual_cost.toFixed(4) : 'free'} />
      </div>
      {stopped}
      {errors.length > 0 && (
        <Card className="mt-3.5">
          <h3 className="mb-1 font-semibold">Problems</h3>
          {errors.map((e, i) => <p key={i} className="text-[13px] text-text-2">{e}</p>)}
        </Card>
      )}
    </>
  )
}

export function DiscoverPage() {
  const [running, setRunning] = useState(false)
  // Poll every 4s only while a run is going.
  const { data, refetch } = useApi<Providers>('/api/discover/providers', { interval: running ? 4000 : undefined })
  const invalidate = useInvalidate()
  const toast = useToast()

  const [provider, setProvider] = useState<string | null>(null)
  const [cities, setCities] = useState('')
  const [limit, setLimit] = useState('100')
  const [depth, setDepth] = useState('30')
  const [cap, setCap] = useState('1.00')
  const [estimate, setEstimate] = useState<{ data: Estimate; cap: number; armed: boolean } | null>(null)
  const [estError, setEstError] = useState('')
  const [pricing, setPricing] = useState(false)
  const [starting, setStarting] = useState(false)
  const [profileStarting, setProfileStarting] = useState(false)

  const wasRunning = useRef(false)
  const kind = useRef<string | undefined>(undefined)
  const isRunning = !!data?.running
  useEffect(() => {
    setRunning(isRunning)
    if (data?.running_kind) kind.current = data.running_kind
    if (wasRunning.current && !isRunning) {
      // A finished run must not leave the button armed from a stale estimate.
      setEstimate(e => e && { ...e, armed: false })
      setStarting(false)
      toast(kind.current === 'profile' ? 'Finished reading websites.' : 'Discovery finished.', 'success')
    }
    wasRunning.current = isRunning
  }, [isRunning, data?.running_kind, toast])

  const head = (
    <SectionHead title="Discover">
      Find businesses matching your ICP. Pick a source, see what it will cost, then run it; nothing is called until you press the button.
    </SectionHead>
  )
  if (data === undefined) return <>{head}<Skeleton /></>
  if (!data || !data.providers) return <>{head}<OfflineState /></>

  const selected = provider || data.selected || data.default
  const body = () => {
    const raw = cities.trim()
    return {
      provider: selected,
      // Semicolons or newlines, never commas: "Denver, CO" is one city.
      cities: raw ? raw.split(/[;\n]/).map(c => c.trim()).filter(Boolean) : [],
      depth: Number(depth) || 30,
      limit: Number(limit) || 100,
      max_spend: Number(cap) || 1,
    }
  }

  const pick = (key: string) => { setProvider(key); setEstimate(null); setEstError('') }

  const doEstimate = async () => {
    setPricing(true); setEstError(''); setEstimate(null)
    const b = body()
    const res = await post<Estimate>('/api/discover/estimate', b)
    setPricing(false)
    if (!res || res.error) { setEstError((res && res.error) || 'Could not estimate.'); return }
    setEstimate({ data: res, cap: b.max_spend, armed: true })
  }

  const doRun = async () => {
    setStarting(true)
    const res = await post<{ success?: boolean; queries?: number; message?: string }>('/api/discover/run', body())
    if (res && res.success) toast(`Discovery started — ${res.queries} queries.`, 'success')
    else { toast((res && res.message) || 'Could not start.', 'error'); setStarting(false) }
    refetch()
  }

  const doStop = async () => {
    const res = await post<{ success?: boolean }>('/api/discover/stop')
    toast(res && res.success ? 'Stopping after the current query.' : failMsg(res, 'Could not stop.'), res && res.success ? 'success' : 'error')
    refetch()
  }

  const doProfile = async () => {
    setProfileStarting(true)
    const res = await post<{ success?: boolean; pending?: number }>('/api/profile/run')
    setProfileStarting(false)
    if (res && res.success) toast(res.pending ? `Reading ${Math.min(res.pending, 200)} websites.` : 'Every site is already read.', 'success')
    else toast(failMsg(res, 'Could not start.'), 'error')
    refetch()
    invalidate('/api/companies')
  }

  const est = estimate?.data
  const over = !!estimate && !!est && est.estimated_cost > estimate.cap
  const runLabel = isRunning || starting ? 'Running…'
    : !estimate || !estimate.armed ? 'Estimate first'
    : over ? 'Over cap'
    : est!.free ? 'Run — free' : `Run — spend up to $${est!.estimated_cost.toFixed(2)}`
  const runDisabled = isRunning || starting || !estimate || !estimate.armed || over
  const unprofiled = data.unprofiled || 0

  return (
    <>
      {head}
      {unprofiled > 0 && (
        <>
          <Subhead>Read websites</Subhead>
          <Card className="flex items-center justify-between gap-4">
            <div>
              <div className="font-medium">{unprofiled} {unprofiled === 1 ? 'company' : 'companies'} not looked at yet</div>
              <Muted>Free, no account needed. Harvey reads each site for who their agency is, what they are missing, and whether they run ads. Up to 200 per run.</Muted>
            </div>
            <Button size="sm" variant="primary" disabled={isRunning} busy={profileStarting} onClick={doProfile}>
              {isRunning ? 'Busy…' : profileStarting ? 'Starting…' : 'Read their sites'}
            </Button>
          </Card>
        </>
      )}

      <Subhead>Source</Subhead>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {data.providers.map(p => (
          <button
            key={p.key} type="button" aria-pressed={p.key === selected} onClick={() => pick(p.key)}
            className={cn('rounded-lg bg-surface p-4 text-left shadow-card', p.key === selected && 'ring-2 ring-accent')}
          >
            <div className="mb-1 flex items-center justify-between gap-2">
              <h3 className="font-semibold">{p.label}</h3>
              <ToneBadge tone={p.configured ? 'good' : 'waiting'}>{p.configured ? 'ready' : 'needs a key'}</ToneBadge>
            </div>
            <p className="mb-2 text-[13px] text-text-2">{p.blurb}</p>
            <div className="flex gap-2 text-[12px]"><span className="w-10 text-text-3">Cost</span><span>{p.cost_note}</span></div>
            <div className="flex gap-2 text-[12px]"><span className="w-10 text-text-3">Free</span><span>{p.free_tier}</span></div>
            {p.needs_key && (
              <div className="flex gap-2 text-[12px]">
                <span className="w-10 text-text-3">Setup</span>
                <span>{(p.env_keys || []).join(', ')} in .env · {safeHref(p.signup_url)
                  ? <a className="text-accent underline" href={safeHref(p.signup_url)} target="_blank" rel="noopener noreferrer" onClick={e => e.stopPropagation()}>get a key</a>
                  : 'get a key'}</span>
              </div>
            )}
            {p.caveat && <p className="mt-2 text-[12px] text-wait">{p.caveat}</p>}
          </button>
        ))}
      </div>

      <Subhead>Run</Subhead>
      <Card>
        <div className="grid gap-x-3 md:grid-cols-5">
          <Field label="Cities" htmlFor="disc-cities" className="md:col-span-2">
            <Input id="disc-cities" value={cities} onChange={e => setCities(e.target.value)} placeholder="From your ICP, or override: Denver, CO; Boulder, CO" />
          </Field>
          <Field label="Results per query" htmlFor="disc-limit">
            <Input id="disc-limit" type="number" min={1} max={1000} value={limit} onChange={e => setLimit(e.target.value)} />
          </Field>
          <Field label="Search depth" htmlFor="disc-depth">
            <Input id="disc-depth" type="number" min={10} max={100} step={10} value={depth} onChange={e => setDepth(e.target.value)} />
          </Field>
          <Field label="Spend cap ($)" htmlFor="disc-cap">
            <Input id="disc-cap" type="number" min={0} step={0.25} value={cap} onChange={e => setCap(e.target.value)} />
          </Field>
        </div>

        <div aria-live="polite" className="mb-3">
          {pricing && <Muted>Pricing it…</Muted>}
          {estError && <Banner>{estError}</Banner>}
          {est && estimate && (
            <div className="rounded-md bg-surface-2 p-4">
              <div className={cn('text-[24px] font-semibold', est.free ? 'text-good' : 'text-wait')}>
                {est.free ? 'Free' : '$' + est.estimated_cost.toFixed(4)}
              </div>
              <Muted>{est.query_count} {est.query_count === 1 ? 'query' : 'queries'}{est.free ? '' : ` · cap is $${estimate.cap.toFixed(2)}`}</Muted>
              {over && <div className="mt-3"><Banner>Over your cap. Raise the cap or narrow the search.</Banner></div>}
              <div className="mt-3 max-h-48 overflow-auto font-mono text-[12px] text-text-2">
                {est.queries.slice(0, 40).map((q, i) => <div key={i}>{q}</div>)}
                {est.queries.length > 40 && <div>… and {est.queries.length - 40} more</div>}
              </div>
            </div>
          )}
        </div>

        <div className="flex gap-2">
          <Button disabled={pricing} onClick={doEstimate}>Estimate cost</Button>
          <Button variant="primary" disabled={runDisabled} onClick={doRun}>{runLabel}</Button>
          {/* Reading sites can't be stopped part-way; only a discovery run can. */}
          {isRunning && data.running_kind !== 'profile' && <Button variant="danger" onClick={doStop}>Stop</Button>}
        </div>
      </Card>

      <Result report={data.last_report} running={isRunning} />
    </>
  )
}
