import { BarChart3 } from 'lucide-react'
import { Card, SectionHead } from '@/components/ui/card'
import { EmptyState, OfflineState, Skeleton } from '@/components/ui/states'
import { formatDate } from '@/lib/format'
import { useApi } from '@/lib/hooks'
import { cn } from '@/lib/utils'

interface Totals { calls?: number; input_tokens?: number; output_tokens?: number; cache_read_tokens?: number }
interface Row extends Totals { [k: string]: any }
interface UsageData {
  quota_provider?: string
  quota?: Record<string, { utilization?: number; resets_at?: string }>
  totals?: { today?: Totals; week?: Totals; month?: Totals }
  by_day?: { day?: string; output_tokens?: number; calls?: number }[]
  by_provider?: Row[]; by_agent?: Row[]; by_task?: Row[]; by_model?: Row[]
  codex_calls_today?: number; claude_calls_today?: number; brain_provider?: string
  brain_status?: { status?: string; provider?: string; reason?: string }
}

function fmtTokens(n?: number) {
  n = n || 0
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k'
  return String(n)
}

const Chip = ({ children, title }: { children: React.ReactNode; title?: string }) => (
  <span title={title} className="rounded-xs border border-line bg-surface-2 px-1.5 py-px text-[12px] text-text-2">{children}</span>
)

/** Calls today per subscription, each labelled with its provider. A paused brain must not look healthy. */
export function BrainChips({ d }: { d: UsageData }) {
  const chip = (label: string, n?: number) => <Chip>{label} calls today <b>{n || 0}</b></Chip>
  const st = d.brain_status || {}
  const codex = d.codex_calls_today || 0
  return (
    <div className="mb-4 flex flex-wrap gap-2">
      {st.status === 'paused' && (
        <Chip title={st.reason}>{st.provider === 'codex' ? 'Codex' : 'Brain'} <b>paused</b>: {(st.reason || '').slice(0, 120)}</Chip>
      )}
      {d.brain_provider === 'codex'
        ? <>{chip('Codex', codex)}{d.claude_calls_today ? chip('Claude', d.claude_calls_today) : null}</>
        : <>{chip('Claude', d.claude_calls_today)}{codex ? chip('Codex', codex) : null}</>}
    </div>
  )
}

const QUOTA_LABELS: Record<string, string> = {
  five_hour: '5-hour window', seven_day: 'Weekly', primary: 'Primary window',
  secondary: 'Secondary window', limit_reached: 'Usage limit reached',
}

export function Quota({ d }: { d: UsageData }) {
  const provider = d.quota_provider
  if (!provider) return null
  const title = provider === 'codex' ? 'Codex (ChatGPT plan) Quota' : 'Claude Subscription Quota'
  const entries = Object.entries(d.quota || {})
  return (
    <Card className="mb-4">
      <h3 className="mb-3 text-[15px] font-semibold">{title}</h3>
      {!entries.length ? (
        <p className="text-[13px] text-text-2">
          {provider === 'codex'
            ? "Quota unavailable: Harvey couldn't read your ChatGPT plan's limits (check codex login, the Codex CLI version, and network)."
            : "Quota unavailable: Harvey couldn't read your Claude subscription's limits (check claude login and network)."}{' '}
          Until it can, budgets count Harvey's own calls.
        </p>
      ) : entries.map(([key, w]) => {
        const pct = Math.min(100, Math.max(0, w.utilization || 0))
        return (
          <div key={key} className="mb-3 last:mb-0">
            <div className="mb-1 flex justify-between text-[13px]">
              <span>{QUOTA_LABELS[key] || key}{w.resets_at ? ' · resets ' + formatDate(w.resets_at) : ''}</span>
              <span className="font-medium tabular-nums">{pct.toFixed(0)}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-surface-3" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={QUOTA_LABELS[key] || key}>
              <div className={cn('h-full rounded-full', pct >= 80 ? 'bg-wait' : 'bg-good')} style={{ width: pct + '%' }} />
            </div>
          </div>
        )
      })}
    </Card>
  )
}

function StatCard({ label, p = {} }: { label: string; p?: Totals }) {
  return (
    <Card>
      <div className="text-[12px] text-text-3">{label}</div>
      <div className="my-1 text-[26px] font-semibold tabular-nums">{p.calls || 0}</div>
      <div className="flex flex-wrap gap-1.5">
        <Chip>calls</Chip>
        <Chip>out <b>{fmtTokens(p.output_tokens)}</b></Chip>
        <Chip>in <b>{fmtTokens(p.input_tokens)}</b></Chip>
        <Chip>cached <b>{fmtTokens(p.cache_read_tokens)}</b></Chip>
      </div>
    </Card>
  )
}

function Breakdown({ title, rows, keyName }: { title: string; rows?: Row[]; keyName: string }) {
  if (!rows?.length) return null
  const max = Math.max(...rows.map(r => r.calls || 0), 1)
  return (
    <Card flush className="mb-4">
      <h3 className="px-5 pt-4 text-[15px] font-semibold">{title}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="text-left text-[12px] text-text-3">
              <th className="px-5 py-2 font-medium">{keyName}</th><th className="py-2 font-medium">Calls</th>
              <th className="py-2 text-right font-medium">Input</th><th className="py-2 text-right font-medium">Output</th>
              <th className="px-5 py-2 text-right font-medium">Cache read</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-line">
                <td className="px-5 py-2 font-medium">{String(r[keyName.toLowerCase()] || '')}</td>
                <td className="py-2">
                  <span className="flex items-center gap-2">
                    <span className="h-1.5 w-[120px] overflow-hidden rounded-full bg-surface-3"><span className="block h-full bg-accent" style={{ width: (100 * (r.calls || 0) / max) + '%' }} /></span>
                    <span className="tabular-nums">{r.calls || 0}</span>
                  </span>
                </td>
                <td className="py-2 text-right tabular-nums text-text-2">{fmtTokens(r.input_tokens)}</td>
                <td className="py-2 text-right tabular-nums">{fmtTokens(r.output_tokens)}</td>
                <td className="px-5 py-2 text-right tabular-nums text-text-2">{fmtTokens(r.cache_read_tokens)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

export function UsagePage() {
  const { data } = useApi<UsageData>('/api/usage', { interval: 15000 })
  // Call counts and the brain's pause live on /api/stats, not /api/usage.
  const { data: stats } = useApi<UsageData>('/api/stats', { interval: 15000 })
  const head = (
    <SectionHead title="Usage">
      Model usage on your subscription (Claude or Codex): calls and tokens by provider, agent and task. Subscription plans aren't billed per token, so there are no dollar costs here.
    </SectionHead>
  )
  if (data === undefined) return <div>{head}<Skeleton /></div>
  if (!data) return <div>{head}<OfflineState /></div>

  const t = data.totals || {}
  const days = (data.by_day || []).slice(-30)
  const maxOut = Math.max(...days.map(d => d.output_tokens || 0), 1)
  const providers = data.by_provider || []
  const showProviders = providers.length > 1 || (providers.length === 1 && providers[0].provider !== 'claude')
  const anyRows = (data.by_agent || []).length || (data.by_task || []).length

  return (
    <div>
      {head}
      <BrainChips d={stats || {}} />
      <Quota d={data} />
      <div className="mb-4 grid gap-4 sm:grid-cols-3">
        <StatCard label="Today" p={t.today} />
        <StatCard label="Last 7 Days" p={t.week} />
        <StatCard label="Last 30 Days" p={t.month} />
      </div>
      {days.length > 0 && (
        <Card className="mb-4">
          <h3 className="mb-3 text-[15px] font-semibold">
            Output tokens per day <span className="ml-1 text-[12px] font-normal text-text-3">last {days.length} days · peak {fmtTokens(maxOut)}</span>
          </h3>
          <div className="flex h-24 items-end gap-0.5" role="img" aria-label="Output tokens per day">
            {days.map((d, i) => (
              <span key={i} className="flex h-full flex-1 items-end" title={`${d.day || ''}: ${fmtTokens(d.output_tokens)} out, ${d.calls || 0} calls`}>
                <i className="block w-full rounded-t-xs bg-accent" style={{ height: Math.max(2, (d.output_tokens || 0) / maxOut * 100) + '%' }} />
              </span>
            ))}
          </div>
          <div className="mt-1 flex justify-between text-[12px] text-text-3"><span>{days[0].day}</span><span>{days[days.length - 1].day}</span></div>
        </Card>
      )}
      {!anyRows ? (
        <EmptyState icon={<BarChart3 className="size-5" />} title="No usage recorded yet">
          Once Harvey starts making model calls (Claude or Codex), every one is logged here with exact calls and tokens by provider, agent and task.
        </EmptyState>
      ) : (
        <>
          {showProviders && <Breakdown title="By Provider (30 days)" rows={providers} keyName="Provider" />}
          <Breakdown title="By Agent (30 days)" rows={data.by_agent} keyName="Agent" />
          <Breakdown title="By Task (30 days)" rows={data.by_task} keyName="Task" />
          <Breakdown title="By Model (30 days)" rows={data.by_model} keyName="Model" />
        </>
      )}
    </div>
  )
}
